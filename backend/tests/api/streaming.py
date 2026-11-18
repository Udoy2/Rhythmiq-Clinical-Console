"""Test helpers: a minimal device-side client for CONTRACT §2.1 and a live-feed collector for §2.2."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import websockets

REPO_ROOT = Path(__file__).resolve().parents[3]
RECORDS = REPO_ROOT / "data" / "records"
FS = 360
FRAME = 36  # 100 ms at 360 Hz


def load_record(record: str, seconds: float) -> tuple[list[int], list[dict]]:
    """First ``seconds`` of ``<record>.csv`` (MLII, raw ADC units) and its beat annotations."""
    n = int(seconds * FS)
    samples: list[int] = []
    with (RECORDS / f"{record}.csv").open() as fh:
        next(fh)
        for line in fh:
            samples.append(int(line.split(",")[1]))
            if len(samples) >= n:
                break
    annotations: list[dict] = []
    with (RECORDS / f"{record}annotations.txt").open() as fh:
        next(fh)
        for line in fh:
            parts = line.split()
            if len(parts) >= 3 and parts[1].isdigit() and int(parts[1]) < n and parts[2] in "NLRBAaJSVrFejnE/fQ?":
                annotations.append({"sample": int(parts[1]), "symbol": parts[2]})
    return samples, annotations


class LiveCollector:
    """Connects to ``/v1/live/{patient}`` and records every message with its arrival time."""

    def __init__(self, ws_base: str, patient_id: str, token: str) -> None:
        self.url = f"{ws_base}/v1/live/{patient_id}?token={token}"
        self.messages: list[tuple[float, dict]] = []
        self._task: asyncio.Task | None = None
        self._ws = None
        self.closed_code: int | None = None

    async def __aenter__(self) -> "LiveCollector":
        self._ws = await websockets.connect(self.url, max_size=None)
        self._task = asyncio.create_task(self._run())
        return self

    async def _run(self) -> None:
        try:
            async for raw in self._ws:
                self.messages.append((time.perf_counter(), json.loads(raw)))
        except websockets.ConnectionClosed:
            pass
        self.closed_code = self._ws.close_code

    async def __aexit__(self, *exc) -> None:
        await self._ws.close()
        if self._task:
            await self._task

    def of(self, kind: str) -> list[dict]:
        return [m for _, m in self.messages if m.get("type") == kind]

    async def wait_for(self, predicate, timeout: float = 30.0) -> dict:
        deadline = time.monotonic() + timeout
        seen = 0
        while time.monotonic() < deadline:
            for _, m in self.messages[seen:]:
                if predicate(m):
                    return m
            seen = len(self.messages)
            await asyncio.sleep(0.02)
        raise AssertionError(f"live message not received within {timeout}s; got types "
                             f"{sorted({m.get('type') for _, m in self.messages})}")


class DeviceStream:
    """Device side of the ingest protocol."""

    def __init__(self, ws_base: str, device_id: str, key: str) -> None:
        self.url = f"{ws_base}/v1/stream/{device_id}?key={key}"
        self.device_id = device_id
        self.seq = 0
        self.t0 = 0
        self.sent_at: list[tuple[int, float]] = []  # (end sample of frame, send time)
        self.ws = None
        self.ack: dict | None = None

    async def open(self, hello: dict) -> dict:
        self.ws = await websockets.connect(self.url, max_size=None)
        await self.ws.send(json.dumps({"type": "hello", "device_id": self.device_id, **hello}))
        self.ack = json.loads(await asyncio.wait_for(self.ws.recv(), 20))
        return self.ack

    async def frame(self, samples: list, *, lead_off: bool = False, annotations: list | None = None,
                    seq: int | None = None, t0: int | None = None, advance: bool = True) -> None:
        msg = {"type": "frame", "seq": self.seq if seq is None else seq, "device_id": self.device_id,
               "t0": self.t0 if t0 is None else t0, "fs": FS, "samples": samples, "lead_off": lead_off,
               "source": "replay"}
        if annotations:
            msg["annotations"] = annotations
        await self.ws.send(json.dumps(msg, separators=(",", ":")))
        if advance:
            self.seq += 1
            self.t0 += len(samples)
            self.sent_at.append((self.t0, time.perf_counter()))

    def skip(self, n: int) -> None:
        self.t0 += n

    async def send_signal(self, samples: list, annotations: list[dict] | None = None, *, speed: float = 0.0,
                          lead_off: bool = False) -> None:
        """Send ``samples`` as 100 ms frames; ``speed`` 0 = unpaced, otherwise x real time."""
        by_frame: dict[int, list[dict]] = {}
        base = self.t0
        for a in annotations or []:
            by_frame.setdefault(a["sample"] // FRAME, []).append({"sample": base + a["sample"], "symbol": a["symbol"]})
        started = time.perf_counter()
        for i in range(0, len(samples), FRAME):
            await self.frame(samples[i:i + FRAME], lead_off=lead_off, annotations=by_frame.get(i // FRAME))
            if speed:
                due = started + (i + FRAME) / FS / speed
                delay = due - time.perf_counter()
                if delay > 0:
                    await asyncio.sleep(delay)

    async def bye(self) -> None:
        await self.ws.send('{"type":"bye"}')
        try:
            await asyncio.wait_for(self.ws.wait_closed(), 20)
        except asyncio.TimeoutError:
            await self.ws.close()

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()
