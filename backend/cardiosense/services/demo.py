"""Demo replay (FR-22): list labelled recordings and run the stream sender as a tracked subprocess.

The sender (``sender/stream_sender.py``) connects back to this server's ingest WebSocket exactly like a
device would, so a replay exercises the real pipeline end to end.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from cardiosense.config import get_settings
from cardiosense.db import utcnow
from cardiosense.services.serialize import iso

log = logging.getLogger("cardiosense.demo")

MITBIH_FS = 360
AAMI = {**dict.fromkeys("NLRej", "N"), **dict.fromkeys("AaJS", "S"), **dict.fromkeys("VE", "V"), "F": "F"}
# BRD §15 "Good replay records"
RECORD_NOTES = {
    "100": "Mostly normal rhythm",
    "119": "Frequent V beats",
    "200": "Frequent V beats, couplets — recommended for the alert demo",
    "208": "Frequent V beats — R-peak detector finds only ~74% of beats here; prefer 119 or 200",
    "233": "Frequent V beats and V runs",
    "220": "S beats",
    "223": "S beats",
    "232": "Known model weakness (S-dominant) — avoid for demos",
}


# documented exit codes of sender/stream_sender.py
SENDER_EXIT_CODES = {3: "rejected by the server (device key, pairing or consent)",
                     4: "gave up after repeated failed reconnects"}


def _scan_annotations(path: Path) -> tuple[dict[str, int], int]:
    """Reference beat counts per AAMI class and the last annotated sample index."""
    counts = {"N": 0, "S": 0, "V": 0, "F": 0}
    last = 0
    with path.open("r", errors="replace") as fh:
        next(fh, None)  # header
        for line in fh:
            parts = line.split()
            if len(parts) < 3 or not parts[1].isdigit():
                continue
            last = max(last, int(parts[1]))
            cls = AAMI.get(parts[2])
            if cls:
                counts[cls] += 1
    return counts, last


@dataclass
class Replay:
    record: str
    dataset: str
    speed: float
    patient_id: str
    device_id: str
    org_id: str
    process: asyncio.subprocess.Process
    log_path: Path
    started_at: datetime = field(default_factory=utcnow)
    exit_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"record": self.record, "dataset": self.dataset, "speed": self.speed, "patient_id": self.patient_id,
                "device_id": self.device_id, "pid": self.process.pid, "started_at": iso(self.started_at),
                "running": self.exit_code is None, "exit_code": self.exit_code,
                "exit_reason": SENDER_EXIT_CODES.get(self.exit_code) if self.exit_code else None}


class DemoManager:
    def __init__(self) -> None:
        self.replays: dict[str, Replay] = {}  # by device id
        self._record_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._watchers: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------ records

    def record_path(self, record: str) -> Path | None:
        path = get_settings().data_dir / f"{record}.csv"
        return path if path.is_file() else None

    def _list_records_sync(self) -> list[dict[str, Any]]:
        data_dir = get_settings().data_dir
        out: list[dict[str, Any]] = []
        if not data_dir.is_dir():
            return out
        for csv_path in sorted(data_dir.glob("*.csv")):
            record = csv_path.stem
            ann = data_dir / f"{record}annotations.txt"
            info: dict[str, Any] = {"record": record, "dataset": "MIT-BIH", "fs": MITBIH_FS,
                                    "has_annotations": ann.is_file(), "duration_s": None, "truth_counts": None,
                                    "description": RECORD_NOTES.get(record)}
            if ann.is_file():
                mtime = ann.stat().st_mtime
                cached = self._record_cache.get(record)
                if cached is None or cached[0] != mtime:
                    counts, last = _scan_annotations(ann)
                    cached = (mtime, {"truth_counts": counts, "duration_s": round(last / MITBIH_FS, 1)})
                    self._record_cache[record] = cached
                info.update(cached[1])
                counts = info["truth_counts"]
                summary = " · ".join(f"{k} {v}" for k, v in counts.items() if v)
                note = RECORD_NOTES.get(record)
                info["description"] = f"{note} — reference beats: {summary}" if note else f"Reference beats: {summary}"
            out.append(info)
        return out

    async def list_records(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_records_sync)

    # ------------------------------------------------------------ replays

    def running_count(self) -> int:
        return sum(1 for r in self.replays.values() if r.exit_code is None)

    def status(self, org_id: str | None = None) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self.replays.values() if org_id is None or r.org_id == org_id]

    async def start(
        self, *, record: str, dataset: str, speed: float, patient_id: str, device_id: str, device_key: str,
        org_id: str, start_s: float = 0.0, duration_s: float | None = None, loop: bool = False,
    ) -> Replay:
        settings = get_settings()
        csv_path = self.record_path(record)
        if csv_path is None:
            raise FileNotFoundError(record)
        if not settings.sender_script.is_file():
            raise RuntimeError("stream sender CLI is not installed (sender/stream_sender.py)")
        await self.stop(device_id=device_id)
        cmd = [
            settings.sender_python, str(settings.sender_script),
            "--file", str(csv_path), "--device", device_id, "--key", device_key,
            "--url", settings.self_ws_url, "--speed", f"{speed:g}", "--record", record, "--dataset", dataset,
            "--quiet",
        ]
        if start_s:
            cmd += ["--start", f"{start_s:g}"]
        if duration_s:
            cmd += ["--duration", f"{duration_s:g}"]
        if loop:
            cmd.append("--loop")
        log_file = tempfile.NamedTemporaryFile(prefix=f"cardiosense-replay-{record}-", suffix=".log", delete=False)
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd, cwd=str(settings.sender_script.parent.parent), stdin=asyncio.subprocess.DEVNULL,
                stdout=log_file, stderr=asyncio.subprocess.STDOUT,
            )
        finally:
            log_file.close()
        replay = Replay(record=record, dataset=dataset, speed=speed, patient_id=patient_id, device_id=device_id,
                        org_id=org_id, process=process, log_path=Path(log_file.name))
        self.replays[device_id] = replay
        watcher = asyncio.create_task(self._watch(replay), name=f"replay-{record}")
        self._watchers.add(watcher)
        watcher.add_done_callback(self._watchers.discard)
        log.info("replay started: record=%s device=%s speed=%g pid=%d", record, device_id, speed, process.pid)
        return replay

    async def _watch(self, replay: Replay) -> None:
        code = await replay.process.wait()
        replay.exit_code = code
        if code not in (0, -15, -2):
            reason = SENDER_EXIT_CODES.get(code, "see log")
            log.warning("replay of record %s exited with code %s (%s; log: %s)", replay.record, code, reason,
                        replay.log_path)
        else:
            replay.log_path.unlink(missing_ok=True)

    async def stop(self, *, device_id: str | None = None, patient_id: str | None = None, record: str | None = None,
                   org_id: str | None = None) -> list[dict[str, Any]]:
        """Stop matching replays (all of the organisation's when no filter is given)."""
        stopped = []
        for replay in list(self.replays.values()):
            if device_id is not None and replay.device_id != device_id:
                continue
            if patient_id is not None and replay.patient_id != patient_id:
                continue
            if record is not None and replay.record != record:
                continue
            if org_id is not None and replay.org_id != org_id:
                continue
            if replay.exit_code is None:
                await self._terminate(replay)
                stopped.append(replay.to_dict())
            self.replays.pop(replay.device_id, None)
        return stopped

    @staticmethod
    async def _terminate(replay: Replay) -> None:
        process = replay.process
        if process.returncode is not None:
            return
        try:
            process.terminate()
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        except ProcessLookupError:
            pass

    async def shutdown(self) -> None:
        await self.stop()
        for watcher in tuple(self._watchers):
            watcher.cancel()
