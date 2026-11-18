"""Asynchronous batched persistence.

The live path only *enqueues* work here and never waits for PostgreSQL. A single background task drains
the queue, groups operations into bulk statements and commits them in one transaction. Under back-pressure
nothing is dropped: batches simply grow.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import insert, update
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.inspection import inspect as sa_inspect

from cardiosense.db import session_factory

log = logging.getLogger("cardiosense.writer")


@dataclass(slots=True)
class Insert:
    model: type
    rows: list[dict[str, Any]]


@dataclass(slots=True)
class Update:
    model: type
    pk: Any
    values: dict[str, Any]


Op = Insert | Update


def _is_connectivity_error(exc: BaseException) -> bool:
    if isinstance(exc, (OperationalError, InterfaceError, OSError, ConnectionError)):
        return True
    return isinstance(exc, DBAPIError) and exc.connection_invalidated


class BatchWriter:
    def __init__(self, linger_s: float = 0.2, max_ops: int = 5000) -> None:
        self._queue: asyncio.Queue[Op] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._linger = linger_s
        self._max_ops = max_ops
        self.rows_written = 0
        self.batches = 0
        self.failed_ops = 0
        self.retries = 0
        self.last_flush_ms = 0.0
        self.max_flush_ms = 0.0
        self.max_queue_depth = 0

    # ------------------------------------------------------------ producer side (never blocks)

    def insert(self, model: type, rows: list[dict[str, Any]] | dict[str, Any]) -> None:
        if isinstance(rows, dict):
            rows = [rows]
        if rows:
            self._put(Insert(model, rows))

    def update(self, model: type, pk: Any, values: dict[str, Any]) -> None:
        if values:
            self._put(Update(model, pk, values))

    def _put(self, op: Op) -> None:
        self._queue.put_nowait(op)
        depth = self._queue.qsize()
        if depth > self.max_queue_depth:
            self.max_queue_depth = depth

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="cardiosense-batch-writer")

    async def drain(self) -> None:
        """Wait until everything enqueued so far is committed."""
        await self._queue.join()

    async def stop(self) -> None:
        if self._task is None:
            return
        try:
            await asyncio.wait_for(self.drain(), timeout=30)
        except asyncio.TimeoutError:
            log.error("writer stopped with %d operations still queued", self._queue.qsize())
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def stats(self) -> dict[str, Any]:
        return {
            "queue_depth": self._queue.qsize(),
            "max_queue_depth": self.max_queue_depth,
            "rows_written": self.rows_written,
            "batches": self.batches,
            "retries": self.retries,
            "failed_ops": self.failed_ops,
            "last_flush_ms": round(self.last_flush_ms, 2),
            "max_flush_ms": round(self.max_flush_ms, 2),
        }

    # ------------------------------------------------------------ consumer

    async def _run(self) -> None:
        while True:
            batch = [await self._queue.get()]
            try:
                await asyncio.sleep(self._linger)  # let a batch accumulate
                while len(batch) < self._max_ops and not self._queue.empty():
                    batch.append(self._queue.get_nowait())
                await self._write_with_retry(batch)
            finally:
                for _ in batch:
                    self._queue.task_done()

    async def _write_with_retry(self, batch: list[Op]) -> None:
        delay = 0.2
        attempt = 0
        while True:
            try:
                await self._write(self._coalesce(batch))
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - classified below
                attempt += 1
                self.retries += 1
                if _is_connectivity_error(exc) or attempt < 3:
                    log.warning("persistence batch failed (%s), retry %d", type(exc).__name__, attempt)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 5.0)
                    continue
                break
        # A data error poisons the batch: write operation by operation so one bad row cannot block the rest.
        for op in batch:
            try:
                await self._write([op])
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.failed_ops += 1
                log.error("dropping unwritable %s operation: %s", op.model.__name__, type(exc).__name__)

    @staticmethod
    def _coalesce(batch: list[Op]) -> list[Op]:
        """Merge inserts per table and updates per row, keeping first-occurrence order."""
        merged: list[Op] = []
        inserts: dict[type, Insert] = {}
        updates: dict[tuple[type, Any], Update] = {}
        for op in batch:
            if isinstance(op, Insert):
                target = inserts.get(op.model)
                if target is None:
                    target = Insert(op.model, [])
                    inserts[op.model] = target
                    merged.append(target)
                target.rows.extend(op.rows)
            else:
                key = (op.model, op.pk)
                existing = updates.get(key)
                if existing is None:
                    existing = Update(op.model, op.pk, {})
                    updates[key] = existing
                    merged.append(existing)
                existing.values.update(op.values)
        return merged

    async def _write(self, ops: list[Op]) -> None:
        started = time.perf_counter()
        rows = 0
        async with session_factory()() as session:
            async with session.begin():
                for op in ops:
                    if isinstance(op, Insert):
                        await session.execute(insert(op.model), op.rows)
                        rows += len(op.rows)
                    else:
                        pk_col = sa_inspect(op.model).primary_key[0]
                        await session.execute(update(op.model).where(pk_col == op.pk).values(**op.values))
                        rows += 1
        elapsed = (time.perf_counter() - started) * 1000
        self.rows_written += rows
        self.batches += 1
        self.last_flush_ms = elapsed
        self.max_flush_ms = max(self.max_flush_ms, elapsed)
