"""In-memory fan-out of live messages to UI subscribers, keyed by patient.

Slow consumers may lose ``signal`` messages (waveform preview) but never beats, windows, alerts,
quality or status messages. Messages are serialised once and shared between subscribers.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any

MAX_PENDING_SIGNAL = 50  # ~5 s of 100 ms frames queued for one consumer before signal is shed
MAX_PENDING_TOTAL = 50_000  # a consumer this far behind is dead: disconnect it


def encode(message: dict[str, Any]) -> str:
    return json.dumps(message, separators=(",", ":"), allow_nan=False, default=str)


class Subscriber:
    __slots__ = ("queue", "pending_signal", "dropped_signal", "closed")

    def __init__(self) -> None:
        self.queue: asyncio.Queue[tuple[bool, str] | None] = asyncio.Queue()
        self.pending_signal = 0
        self.dropped_signal = 0
        self.closed = False

    def offer(self, text: str, droppable: bool) -> None:
        if self.closed:
            return
        if droppable:
            if self.pending_signal >= MAX_PENDING_SIGNAL:
                self.dropped_signal += 1
                return
            self.pending_signal += 1
        elif self.queue.qsize() >= MAX_PENDING_TOTAL:
            self.close()
            return
        self.queue.put_nowait((droppable, text))

    async def next(self) -> str | None:
        """Next message to send, or ``None`` when the subscriber has been closed."""
        item = await self.queue.get()
        if item is None:
            return None
        droppable, text = item
        if droppable:
            self.pending_signal -= 1
        return text

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.queue.put_nowait(None)


class LiveHub:
    def __init__(self) -> None:
        self._subs: dict[str, set[Subscriber]] = defaultdict(set)
        self.published = 0
        self.dropped_signal = 0

    def subscribe(self, patient_id: str) -> Subscriber:
        sub = Subscriber()
        self._subs[patient_id].add(sub)
        return sub

    def unsubscribe(self, patient_id: str, sub: Subscriber) -> None:
        self.dropped_signal += sub.dropped_signal
        subs = self._subs.get(patient_id)
        if subs is not None:
            subs.discard(sub)
            if not subs:
                self._subs.pop(patient_id, None)
        sub.close()

    def has_subscribers(self, patient_id: str) -> bool:
        return bool(self._subs.get(patient_id))

    def publish(self, patient_id: str, message: dict[str, Any], *, droppable: bool = False) -> str | None:
        """Send ``message`` to every subscriber of the patient. Returns the encoded text (if anyone listens)."""
        subs = self._subs.get(patient_id)
        if not subs:
            return None
        text = encode(message)
        self.publish_text(patient_id, text, droppable=droppable)
        return text

    def publish_text(self, patient_id: str, text: str, *, droppable: bool = False) -> None:
        subs = self._subs.get(patient_id)
        if not subs:
            return
        self.published += 1
        for sub in tuple(subs):
            sub.offer(text, droppable)

    def stats(self) -> dict[str, int]:
        live = sum(s.dropped_signal for subs in self._subs.values() for s in subs)
        return {
            "subscribers": sum(len(s) for s in self._subs.values()),
            "patients_watched": len(self._subs),
            "published": self.published,
            "signal_dropped_for_slow_consumers": self.dropped_signal + live,
        }
