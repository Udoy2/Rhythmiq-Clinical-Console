"""Device/replay stream ingestion (CONTRACT §2.1) and the in-memory live state of every active session.

Live path per frame:  receive -> StreamProcessor.push (worker thread) -> fan out to UI subscribers ->
enqueue persistence. Nothing on that path awaits PostgreSQL; the database is only touched when a
connection is opened (authentication, session row).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select

from cardiosense.config import DEVICE_TASK_RULES, TIER_RANK, get_settings
from cardiosense.db import new_id, session_factory, utcnow
from cardiosense.models import (
    Alert,
    Beat,
    Device,
    MonitoringSession,
    Patient,
    RhythmWindow,
    RuleConfig,
    SessionGap,
    SignalChunk,
)
from cardiosense.security import verify_device_key
from cardiosense.services.hub import LiveHub, encode
from cardiosense.services.notify import Notifier
from cardiosense.services.pipeline import PipelineProvider, StreamProcessorLike
from cardiosense.services.serialize import alert_to_dict, iso, session_message, session_to_dict
from cardiosense.services.writer import BatchWriter

log = logging.getLogger("cardiosense.ingest")

SIGNAL_BACKLOG_S = 10
BEAT_BACKLOG_S = 60
FULL_WINDOW_S = 9.5
CLOSE_POLICY = 1008  # auth / consent / protocol violation: the sender must not reconnect
CLOSE_SUPERSEDED = 4409  # a newer connection for the same device took over
CLOSE_INTERNAL = 1011
QUALITY_TO_STATUS = {"ok": "streaming", "lead_off": "lead_off", "low_sqi": "low_sqi", "gap": "gap"}


class LatencyTracker:
    """Rolling latency samples (milliseconds) per metric."""

    def __init__(self, maxlen: int = 20_000) -> None:
        self._samples: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=maxlen))

    def add(self, name: str, ms: float) -> None:
        self._samples[name].append(ms)

    def reset(self) -> None:
        self._samples.clear()

    def summary(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for name, values in self._samples.items():
            if not values:
                continue
            arr = np.fromiter(values, dtype=np.float64)
            p50, p95, p99 = np.percentile(arr, [50, 95, 99])
            out[name] = {"n": int(arr.size), "p50": round(float(p50), 2), "p95": round(float(p95), 2),
                         "p99": round(float(p99), 2), "max": round(float(arr.max()), 2)}
        return out


class ChunkBuffer:
    """Accumulates contiguous samples and cuts them into ~chunk-second ``signal_chunks`` rows."""

    def __init__(self, session_id: str, kind: str, chunk_s: float) -> None:
        self.session_id, self.kind, self.chunk_s = session_id, kind, chunk_s
        self.t0 = 0
        self.fs = 0
        self.parts: list[np.ndarray] = []
        self.n = 0
        self.lead_off = False

    def add(self, t0: int, fs: int, values: np.ndarray, lead_off: bool) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if self.n and (t0 != self.t0 + self.n or fs != self.fs):
            rows.append(self._cut())  # discontinuity: never stitch across a gap
        if not self.n:
            self.t0, self.fs = t0, fs
        self.parts.append(values)
        self.n += len(values)
        self.lead_off = self.lead_off or lead_off
        if self.n >= max(1, int(self.fs * self.chunk_s)):
            rows.append(self._cut())
        return rows

    def flush(self) -> list[dict[str, Any]]:
        return [self._cut()] if self.n else []

    def _cut(self) -> dict[str, Any]:
        data = np.concatenate(self.parts).astype("<f4", copy=False).tobytes()
        row = {"session_id": self.session_id, "kind": self.kind, "t0": self.t0, "n": self.n, "fs": self.fs,
               "lead_off": self.lead_off, "data": data}
        self.parts, self.n, self.lead_off = [], 0, False
        return row


@dataclass
class StreamAuth:
    device_id: str
    org_id: str
    patient_id: str
    is_demo: bool
    rules_config: dict[str, Any] | None
    rules_config_version: int | None
    open_alerts: dict[str, dict[str, Any]]


@dataclass
class LiveSession:
    id: str
    device_id: str
    patient_id: str
    org_id: str
    is_demo: bool
    fs: int
    sp: StreamProcessorLike
    ws: WebSocket
    info: dict[str, Any]  # session_to_dict shape
    display: ChunkBuffer
    raw: ChunkBuffer
    started_mono: float = field(default_factory=time.monotonic)
    task: asyncio.Task[Any] | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    closed: bool = False
    failed: bool = False
    # ordering
    last_seq: int = -1
    next_t0: int = 0
    last_frame_at: datetime | None = None
    arrivals: deque[tuple[int, float]] = field(default_factory=deque)
    # backlog for late-joining UIs (already encoded)
    signal_backlog: deque[tuple[int, str]] = field(default_factory=deque)
    beat_backlog: deque[tuple[int, str]] = field(default_factory=deque)
    window_text: str | None = None
    quality_text: str | None = None
    quality_state: str = "ok"
    # counters
    frames: int = 0
    duplicates: int = 0
    out_of_order: int = 0
    bad_frames: int = 0
    gaps: int = 0
    missing_samples: int = 0
    errors: int = 0
    beats: int = 0
    labels: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    truth_matched: int = 0
    truth_agree: int = 0
    windows: int = 0
    hr_sum: float = 0.0
    hr_n: int = 0
    sqi_sum: float = 0.0
    sqi_n: int = 0
    max_risk: int = 0
    max_tier: str = "green"
    alerts: int = 0

    def summary(self) -> dict[str, Any]:
        labelled = sum(v for k, v in self.labels.items() if k != "Unknown")
        out: dict[str, Any] = {
            "duration_s": round(self.next_t0 / self.fs, 3) if self.fs else 0.0,
            "frames": self.frames,
            "duplicates_dropped": self.duplicates,
            "out_of_order_dropped": self.out_of_order,
            "gaps": self.gaps,
            "missing_samples": self.missing_samples,
            "beats": self.beats,
            "beat_counts": {k: int(self.labels.get(k, 0)) for k in ("N", "S", "V", "F", "Unknown")},
            "s_burden_pct": round(100 * self.labels.get("S", 0) / labelled, 2) if labelled else 0.0,
            "v_burden_pct": round(100 * self.labels.get("V", 0) / labelled, 2) if labelled else 0.0,
            "windows": self.windows,
            "mean_hr_bpm": round(self.hr_sum / self.hr_n, 1) if self.hr_n else None,
            "mean_sqi": round(self.sqi_sum / self.sqi_n, 3) if self.sqi_n else None,
            "max_risk_score": self.max_risk,
            "max_risk_tier": self.max_tier,
            "alerts": self.alerts,
            "truth_matched": self.truth_matched,
            "truth_agreement_pct": round(100 * self.truth_agree / self.truth_matched, 2) if self.truth_matched else None,
        }
        try:
            core_stats = self.sp.stats()
            if isinstance(core_stats, dict):
                out["pipeline"] = core_stats
        except Exception:  # noqa: BLE001 - stats are best effort
            pass
        return out


class IngestManager:
    def __init__(self, hub: LiveHub, writer: BatchWriter, pipeline: PipelineProvider, notifier: Notifier) -> None:
        self.hub, self.writer, self.pipeline, self.notifier = hub, writer, pipeline, notifier
        self.sessions: dict[str, LiveSession] = {}  # by device id
        self.by_patient: dict[str, LiveSession] = {}
        self.open_alerts: dict[str, dict[str, dict[str, Any]]] = {}  # patient -> rule -> alert (state "open")
        self.latency = LatencyTracker()
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._pool = ThreadPoolExecutor(max_workers=get_settings().pipeline_threads, thread_name_prefix="pipeline")
        self.total_sessions = 0
        self.total_frames = 0
        self.rejected = 0

    # ================================================================ connection lifecycle

    async def handle_connection(self, ws: WebSocket, device_id: str, key: str) -> None:
        settings = get_settings()
        auth = await self._authenticate(device_id, key)
        if isinstance(auth, str):
            await self._reject(ws, auth)
            return
        try:
            hello = json.loads(await asyncio.wait_for(ws.receive_text(), settings.hello_timeout_s))
        except (asyncio.TimeoutError, ValueError, WebSocketDisconnect, RuntimeError):
            await self._reject(ws, "expected a hello message")
            return
        problem = self._validate_hello(hello, device_id)
        if problem:
            await self._reject(ws, problem)
            return
        if hello.get("source") == "replay" and not auth.is_demo:
            # Ground-truth replays are demo data by definition and are never mixed into a real patient's record.
            await self._reject(ws, "replay streams are only accepted for demo patients")
            return
        if not self.pipeline.available:
            await self._reject(ws, self.pipeline.error or "pipeline core unavailable", code=CLOSE_INTERNAL)
            return
        async with self._locks[device_id]:
            previous = self.sessions.get(device_id)
            if previous is not None:
                await self._supersede(previous)
            try:
                ls = await self._open_session(auth, hello, ws)
            except Exception as exc:  # noqa: BLE001
                log.error("could not open session for device %s: %s", device_id, type(exc).__name__)
                await self._reject(ws, "could not start session", code=CLOSE_INTERNAL)
                return
        ls.task = asyncio.current_task()
        try:
            await ws.send_text(encode({"type": "ack", "session_id": ls.id, "model_loaded": self.pipeline.model_loaded}))
        except (WebSocketDisconnect, RuntimeError):
            await self._close_session(ls, "disconnected")
            return
        await self._run_session(ls, ws)

    async def _reject(self, ws: WebSocket, detail: str, code: int = CLOSE_POLICY) -> None:
        self.rejected += 1
        try:
            await ws.send_text(encode({"type": "error", "detail": detail}))
            await ws.close(code=code, reason=detail[:120])
        except (WebSocketDisconnect, RuntimeError):
            pass

    @staticmethod
    def _validate_hello(hello: Any, device_id: str) -> str | None:
        if not isinstance(hello, dict) or hello.get("type") != "hello":
            return "first message must be a hello"
        if hello.get("device_id") not in (None, device_id):
            return "hello.device_id does not match the stream URL"
        fs = hello.get("fs")
        if not isinstance(fs, (int, float)) or isinstance(fs, bool) or not 20 <= fs <= 4000:
            return "hello.fs must be a sample rate between 20 and 4000 Hz"
        adc = hello.get("adc")
        if adc is not None:
            if not isinstance(adc, dict):
                return "hello.adc must be an object"
            gain = adc.get("gain", 1.0)
            if not isinstance(gain, (int, float)) or gain == 0 or not isinstance(adc.get("zero", 0), (int, float)):
                return "hello.adc needs numeric zero and non-zero gain"
        if hello.get("source", "serial") not in ("serial", "replay"):
            return "hello.source must be serial or replay"
        return None

    async def _authenticate(self, device_id: str, key: str) -> StreamAuth | str:
        async with session_factory()() as db:
            device = await db.get(Device, device_id)
            if device is None or not verify_device_key(key, device.key_hash):
                return "unknown device or invalid device key"
            if device.patient_id is None:
                return "device is not paired to a patient"
            patient = await db.get(Patient, device.patient_id)
            if patient is None:
                return "device is not paired to a patient"
            if not patient.consent:
                return "patient consent is required before streaming"
            rule_cfg = (
                await db.execute(
                    select(RuleConfig).where(RuleConfig.org_id == patient.org_id).order_by(RuleConfig.version.desc()).limit(1)
                )
            ).scalar_one_or_none()
            open_rows = (
                await db.execute(select(Alert).where(Alert.patient_id == patient.id, Alert.state == "open"))
            ).scalars().all()
            return StreamAuth(
                device_id=device.id,
                org_id=patient.org_id,
                patient_id=patient.id,
                is_demo=patient.is_demo,
                rules_config=rule_cfg.config if rule_cfg else None,
                rules_config_version=rule_cfg.version if rule_cfg else None,
                open_alerts={a.rule_id: alert_to_dict(a) for a in open_rows},
            )

    async def _open_session(self, auth: StreamAuth, hello: dict[str, Any], ws: WebSocket) -> LiveSession:
        settings = get_settings()
        fs = int(round(hello["fs"]))
        adc = hello.get("adc")
        adc_zero = float(adc.get("zero", 0.0)) if adc else 0.0  # no adc => samples are already mV
        adc_gain = float(adc.get("gain", 1.0)) if adc else 1.0
        loop = asyncio.get_running_loop()
        sp = await loop.run_in_executor(
            self._pool,
            lambda: self.pipeline.create(fs=fs, adc_zero=adc_zero, adc_gain=adc_gain, rules_config=auth.rules_config),
        )
        source = hello.get("source", "serial")
        row = MonitoringSession(
            id=new_id(),
            patient_id=auth.patient_id,
            device_id=auth.device_id,
            source=source,
            record=str(hello["record"])[:32] if hello.get("record") is not None else None,
            dataset=str(hello["dataset"])[:32] if hello.get("dataset") is not None else None,
            fs=fs,
            adc_zero=adc_zero if adc else None,
            adc_gain=adc_gain if adc else None,
            firmware=str(hello.get("firmware"))[:64] if hello.get("firmware") else None,
            speed=float(hello["speed"]) if isinstance(hello.get("speed"), (int, float)) else None,
            model_version=getattr(sp, "model_version", None) or self.pipeline.model_version,
            rule_version=getattr(sp, "rule_version", None) or self.pipeline.rule_version,
            rule_config_version=auth.rules_config_version,
            model_loaded=self.pipeline.model_loaded,
            is_demo=auth.is_demo,
            started_at=utcnow(),
            stats={},
        )
        async with session_factory()() as db:
            db.add(row)
            device = await db.get(Device, auth.device_id)
            if device is not None:
                device.firmware, device.fs, device.last_seen, device.task = row.firmware, fs, row.started_at, None
            patient = await db.get(Patient, auth.patient_id)
            if patient is not None:
                patient.signal_status = "streaming"
            await db.commit()
        ls = LiveSession(
            id=row.id, device_id=auth.device_id, patient_id=auth.patient_id, org_id=auth.org_id,
            is_demo=auth.is_demo, fs=fs, sp=sp, ws=ws, info=session_to_dict(row, active=True),
            display=ChunkBuffer(row.id, "display", settings.signal_chunk_s),
            raw=ChunkBuffer(row.id, "raw", settings.signal_chunk_s),
        )
        self.sessions[auth.device_id] = ls
        self.by_patient[auth.patient_id] = ls
        self.open_alerts[auth.patient_id] = auth.open_alerts
        self.total_sessions += 1
        self.hub.publish(ls.patient_id, session_message(ls.info))
        self.hub.publish(ls.patient_id, {"type": "status", "stream": "connected", "last_sample_time": None})
        log.info("session %s started: device=%s source=%s fs=%d", ls.id, ls.device_id, source, fs)
        return ls

    async def _supersede(self, old: LiveSession) -> None:
        """A new connection for the same device ends the old session."""
        try:
            await old.ws.close(code=CLOSE_SUPERSEDED, reason="superseded by a new connection")
        except Exception:  # noqa: BLE001 - already closed
            pass
        try:
            await asyncio.wait_for(old.done.wait(), timeout=5)
        except asyncio.TimeoutError:
            if old.task is not None:
                old.task.cancel()
            await self._close_session(old, "superseded")

    async def _run_session(self, ls: LiveSession, ws: WebSocket) -> None:
        settings = get_settings()
        queue: asyncio.Queue[tuple[float, dict[str, Any]] | None] = asyncio.Queue(maxsize=1000)
        processor = asyncio.create_task(self._processor(ls, queue), name=f"ingest-{ls.id[:8]}")
        reason = "disconnected"
        try:
            while not ls.failed:
                try:
                    text = await asyncio.wait_for(ws.receive_text(), settings.stream_idle_timeout_s)
                except asyncio.TimeoutError:
                    reason = "idle timeout"
                    break
                received = time.perf_counter()
                try:
                    msg = json.loads(text)
                except ValueError:
                    ls.bad_frames += 1
                    continue
                kind = msg.get("type") if isinstance(msg, dict) else None
                if kind == "frame":
                    await queue.put((received, msg))  # bounded: back-pressure reaches the sender, nothing is dropped
                elif kind == "bye":
                    reason = "bye"
                    break
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            await queue.put(None)
            try:
                await processor
            except Exception:  # noqa: BLE001
                pass
            await self._close_session(ls, "processing error" if ls.failed else reason)
            try:
                await ws.close()
            except Exception:  # noqa: BLE001 - already closed by the peer
                pass

    async def _processor(self, ls: LiveSession, queue: asyncio.Queue[tuple[float, dict[str, Any]] | None]) -> None:
        while True:
            item = await queue.get()
            if item is None:
                return
            if ls.failed:
                continue  # drain so the reader is never blocked
            received, frame = item
            try:
                await self._process_frame(ls, frame, received)
            except Exception as exc:  # noqa: BLE001 - never log payloads (no ECG in logs)
                ls.errors += 1
                log.error("session %s: frame processing failed: %s", ls.id, type(exc).__name__, exc_info=ls.errors == 1)
                if ls.errors >= 10:
                    ls.failed = True
                    try:
                        await ls.ws.send_text(encode({"type": "error", "detail": "stream processing failed"}))
                        await ls.ws.close(code=CLOSE_INTERNAL)
                    except Exception:  # noqa: BLE001
                        pass

    # ================================================================ per-frame live path

    async def _process_frame(self, ls: LiveSession, frame: dict[str, Any], received: float) -> None:
        seq, t0, samples = frame.get("seq"), frame.get("t0"), frame.get("samples")
        if not isinstance(seq, int) or not isinstance(t0, int) or t0 < 0 or not isinstance(samples, list) or not samples:
            ls.bad_frames += 1
            return
        if seq <= ls.last_seq:
            ls.duplicates += 1  # duplicate sequence numbers are dropped
            return
        try:
            values = np.asarray(samples, dtype=np.float64)
        except (TypeError, ValueError):
            ls.bad_frames += 1
            return
        if values.ndim != 1 or values.size > ls.fs * 10 or not np.all(np.isfinite(values)):
            ls.bad_frames += 1
            return
        ls.last_seq = seq
        if t0 < ls.next_t0:  # overlap: samples already ingested are never re-processed
            values = values[ls.next_t0 - t0:]
            t0 = ls.next_t0
            if not values.size:
                ls.out_of_order += 1
                return
        if t0 > ls.next_t0:  # a jump in t0 is a gap: recorded, never interpolated
            missing = t0 - ls.next_t0
            ls.gaps += 1
            ls.missing_samples += missing
            self.writer.insert(SessionGap, {"session_id": ls.id, "at_sample": ls.next_t0, "missing": missing})
        lead_off = bool(frame.get("lead_off", False))
        annotations = frame.get("annotations")
        if not isinstance(annotations, list):
            annotations = None
        ls.next_t0 = t0 + values.size
        ls.frames += 1
        self.total_frames += 1
        ls.last_frame_at = utcnow()
        ls.arrivals.append((ls.next_t0, received))

        loop = asyncio.get_running_loop()
        pushed = time.perf_counter()
        out = await loop.run_in_executor(self._pool, ls.sp.push, values, t0, lead_off, annotations)
        self.latency.add("push_ms", (time.perf_counter() - pushed) * 1000)

        self.writer.insert(SignalChunk, ls.raw.add(t0, ls.fs, values, lead_off))
        self._emit(ls, out, received)

    def _emit(self, ls: LiveSession, out: Any, received: float | None) -> None:
        """Fan a PipelineOutput out to subscribers and enqueue its persistence. Synchronous: no awaits."""
        pid = ls.patient_id
        hub, writer = self.hub, self.writer

        signal = getattr(out, "signal", None)
        if signal and signal.get("mv") is not None and len(signal["mv"]):
            mv = signal["mv"]
            fs = int(signal.get("fs") or ls.fs)
            t0 = int(signal["t0"])
            lead_off = bool(signal.get("lead_off", False))
            text = encode({"type": "signal", "t0": t0, "fs": fs, "mv": mv if isinstance(mv, list) else list(mv),
                           "lead_off": lead_off})
            hub.publish_text(pid, text, droppable=True)
            end = t0 + len(mv)
            ls.signal_backlog.append((end, text))
            horizon = end - SIGNAL_BACKLOG_S * fs
            while ls.signal_backlog and ls.signal_backlog[0][0] <= horizon:
                ls.signal_backlog.popleft()
            writer.insert(SignalChunk, ls.display.add(t0, fs, np.asarray(mv, dtype="<f4"), lead_off))

        beats = getattr(out, "beats", None) or []
        if beats:
            rows = []
            now = time.perf_counter()
            for b in beats:
                text = encode({"type": "beat", **b})
                hub.publish_text(pid, text)
                sample = int(b["sample"])
                ls.beat_backlog.append((sample, text))
                rows.append({
                    "session_id": ls.id, "sample": sample, "time_s": float(b["time_s"]), "label": b["label"],
                    "confidence": b.get("confidence"), "probs": b.get("probs"), "truth": b.get("truth"),
                    "model_version": b.get("model_version"),
                })
                ls.beats += 1
                ls.labels[b["label"]] += 1
                if b.get("truth") is not None:
                    ls.truth_matched += 1
                    ls.truth_agree += b["truth"] == b["label"]
                # acquisition-side latency: arrival of the frame that carried the R-peak -> label broadcast
                while ls.arrivals and ls.arrivals[0][0] <= sample:
                    ls.arrivals.popleft()
                if ls.arrivals and received is not None:
                    self.latency.add("rpeak_frame_to_beat_broadcast_ms", (now - ls.arrivals[0][1]) * 1000)
            horizon = int(beats[-1]["sample"]) - BEAT_BACKLOG_S * ls.fs
            while ls.beat_backlog and ls.beat_backlog[0][0] < horizon:
                ls.beat_backlog.popleft()
            writer.insert(Beat, rows)
            if received is not None:
                self.latency.add("frame_to_beat_broadcast_ms", (now - received) * 1000)

        for q in getattr(out, "quality", None) or []:
            state = q.get("state", "ok")
            ls.quality_state = state
            ls.quality_text = encode({"type": "quality", **q})
            hub.publish_text(pid, ls.quality_text)
            writer.update(Patient, pid, {"signal_status": QUALITY_TO_STATUS.get(state, state)})
            if state == "ok":
                writer.update(Device, ls.device_id, {"task": None})

        for task in getattr(out, "tasks", None) or []:
            self._device_task(ls, task)

        for w in getattr(out, "windows", None) or []:
            self._on_window(ls, w, final=received is None)

        candidates = getattr(out, "alerts", None) or []
        for cand in candidates:
            if cand.get("rule_id") in DEVICE_TASK_RULES:
                self._device_task(ls, cand)  # device problems are tasks, never clinical alerts
            else:
                self._on_alert_candidate(ls, cand)
        if candidates and received is not None:
            self.latency.add("frame_to_alert_broadcast_ms", (time.perf_counter() - received) * 1000)

    def _on_window(self, ls: LiveSession, w: dict[str, Any], final: bool = False) -> None:
        wid = new_id()
        now = utcnow()
        ls.window_text = encode({"type": "window", "id": wid, **w})
        self.hub.publish_text(ls.patient_id, ls.window_text)
        risk = w.get("risk") or {}
        score = int(round(risk.get("score") or 0))
        tier = risk.get("tier") or "green"
        findings = w.get("findings") or []
        self.writer.insert(RhythmWindow, {
            "id": wid, "session_id": ls.id, "patient_id": ls.patient_id, "t": now,
            "start_s": float(w["start_s"]), "end_s": float(w["end_s"]), "hr_bpm": w.get("hr_bpm"),
            "sdnn_ms": w.get("sdnn_ms"), "rmssd_ms": w.get("rmssd_ms"),
            "s_burden_pct": w.get("s_burden_pct"), "v_burden_pct": w.get("v_burden_pct"), "sqi": w.get("sqi"),
            "beat_counts": w.get("beat_counts") or {}, "findings": findings, "risk": risk,
            "risk_score": score, "risk_tier": tier, "rule_version": w.get("rule_version"),
        })
        ls.windows += 1
        if w.get("hr_bpm") is not None:
            ls.hr_sum += float(w["hr_bpm"])
            ls.hr_n += 1
        if w.get("sqi") is not None:
            ls.sqi_sum += float(w["sqi"])
            ls.sqi_n += 1
        if score > ls.max_risk:
            ls.max_risk = score
        if TIER_RANK.get(tier, 0) > TIER_RANK[ls.max_tier]:
            ls.max_tier = tier
        patient_values: dict[str, Any] = {"risk_score": score, "risk_tier": tier}
        notable = [f for f in findings if TIER_RANK.get(f.get("severity"), 0) >= 1 and f.get("rule_id") not in DEVICE_TASK_RULES]
        if notable:
            top = max(notable, key=lambda f: TIER_RANK.get(f.get("severity"), 0))
            patient_values["last_event"] = {"label": top.get("label") or top.get("rule_id"), "tier": top.get("severity"),
                                            "rule_id": top.get("rule_id"), "time": iso(now)}
        # A truncated window closed by the end-of-stream flush is stored and shown, but it does not set the
        # patient's standing risk: a few seconds of signal are not evidence for a tier change.
        truncated = final and float(w["end_s"]) - float(w["start_s"]) < FULL_WINDOW_S
        if not truncated:
            self.writer.update(Patient, ls.patient_id, patient_values)
        self.writer.update(Device, ls.device_id, {"sqi": w.get("sqi"), "last_seen": now})
        self.writer.update(MonitoringSession, ls.id, {"last_sample": ls.next_t0, "stats": ls.summary()})

    def _device_task(self, ls: LiveSession, task: dict[str, Any]) -> None:
        value = {"rule_id": task.get("rule_id"), "title": task.get("title") or task.get("label") or task.get("rule_id"),
                 "detail": task.get("detail"), "time": iso(utcnow())}
        self.writer.update(Device, ls.device_id, {"task": value})

    def _on_alert_candidate(self, ls: LiveSession, cand: dict[str, Any]) -> None:
        rule_id = cand["rule_id"]
        now = utcnow()
        cache = self.open_alerts.setdefault(ls.patient_id, {})
        existing = cache.get(rule_id)
        if existing is not None:
            # Deduplicate: one open alert per patient and rule. A recurrence updates it instead of adding a row.
            existing["occurrences"] = int(existing.get("occurrences") or 1) + 1
            existing["updated_at"] = existing["last_seen_at"] = iso(now)
            values: dict[str, Any] = {"occurrences": existing["occurrences"], "updated_at": now, "last_seen_at": now}
            if TIER_RANK.get(cand.get("tier"), 0) > TIER_RANK.get(existing["tier"], 0):
                existing["tier"] = values["tier"] = cand["tier"]
                existing["title"] = values["title"] = cand.get("title") or existing["title"]
                existing["detail"] = values["detail"] = cand.get("detail")
            if existing.get("session_id") != ls.id:
                # The open alert stems from an earlier session: show the current episode's evidence.
                evidence = dict(cand.get("evidence") or {})
                evidence.setdefault("rule_version", ls.info["rule_version"])
                evidence.setdefault("model_version", ls.info["model_version"])
                existing.update(session_id=ls.id, evidence=evidence, detail=cand.get("detail"),
                                rule_version=evidence.get("rule_version"), model_version=evidence.get("model_version"))
                values.update(session_id=ls.id, evidence=evidence, detail=cand.get("detail"),
                              rule_version=evidence.get("rule_version"), model_version=evidence.get("model_version"))
                ls.alerts += 1
                self.notifier.alert_created(existing, ls.org_id)
            self.writer.update(Alert, existing["id"], values)
            self.hub.publish(ls.patient_id, {"type": "alert", "alert": existing})
            return
        evidence = dict(cand.get("evidence") or {})
        evidence.setdefault("rule_version", ls.info["rule_version"])
        evidence.setdefault("model_version", ls.info["model_version"])
        row = {
            "id": new_id(), "org_id": ls.org_id, "patient_id": ls.patient_id, "session_id": ls.id,
            "rule_id": rule_id, "tier": cand.get("tier") or "amber", "title": cand.get("title") or rule_id,
            "detail": cand.get("detail"), "state": "open", "is_demo": ls.is_demo, "created_at": now,
            "updated_at": now, "occurrences": 1, "last_seen_at": now, "history": [], "evidence": evidence,
            "rule_version": evidence.get("rule_version"), "model_version": evidence.get("model_version"),
        }
        alert = alert_to_dict(Alert(**row))
        cache[rule_id] = alert
        ls.alerts += 1
        self.writer.insert(Alert, row)
        self.writer.update(Patient, ls.patient_id, {
            "last_event": {"label": row["title"], "tier": row["tier"], "rule_id": rule_id, "time": iso(now),
                           "alert_id": row["id"]},
        })
        self.hub.publish(ls.patient_id, {"type": "alert", "alert": alert})
        self.notifier.alert_created(alert, ls.org_id)
        log.info("alert %s raised: rule=%s tier=%s session=%s", row["id"], rule_id, row["tier"], ls.id)

    # ================================================================ session end

    async def _close_session(self, ls: LiveSession, reason: str) -> None:
        if ls.closed:
            return
        ls.closed = True
        try:
            loop = asyncio.get_running_loop()
            out = await loop.run_in_executor(self._pool, ls.sp.flush)
            self._emit(ls, out, None)
        except Exception as exc:  # noqa: BLE001
            log.error("session %s: flush failed: %s", ls.id, type(exc).__name__)
        self.writer.insert(SignalChunk, ls.raw.flush())
        self.writer.insert(SignalChunk, ls.display.flush())
        ended = utcnow()
        stats = ls.summary()
        stats["end_reason"] = reason
        self.writer.update(MonitoringSession, ls.id, {"ended_at": ended, "last_sample": ls.next_t0, "stats": stats})
        if self.sessions.get(ls.device_id) is ls:
            del self.sessions[ls.device_id]
        if self.by_patient.get(ls.patient_id) is ls:
            del self.by_patient[ls.patient_id]
            self.open_alerts.pop(ls.patient_id, None)
            self.writer.update(Patient, ls.patient_id, {"signal_status": "offline"})
        ls.info.update(active=False, ended_at=iso(ended), stats=stats, duration_s=stats["duration_s"])
        self.hub.publish(ls.patient_id, session_message(ls.info))
        self.hub.publish(ls.patient_id, {"type": "status", "stream": "disconnected",
                                         "last_sample_time": iso(ls.last_frame_at)})
        ls.done.set()
        log.info("session %s ended (%s): frames=%d beats=%d windows=%d alerts=%d gaps=%d",
                 ls.id, reason, ls.frames, ls.beats, ls.windows, ls.alerts, ls.gaps)

    async def stop_patient(self, patient_id: str, reason: str) -> int:
        """End every active stream of a patient (e.g. consent withdrawn). Returns how many were stopped."""
        stopped = 0
        for ls in [s for s in self.sessions.values() if s.patient_id == patient_id]:
            try:
                await ls.ws.send_text(encode({"type": "error", "detail": reason}))
                await ls.ws.close(code=CLOSE_POLICY, reason=reason[:120])
            except Exception:  # noqa: BLE001 - already gone
                pass
            try:
                await asyncio.wait_for(ls.done.wait(), timeout=5)
            except asyncio.TimeoutError:
                await self._close_session(ls, reason)
            stopped += 1
        return stopped

    async def shutdown(self) -> None:
        for ls in tuple(self.sessions.values()):
            try:
                await ls.ws.close(code=1001, reason="server shutting down")
            except Exception:  # noqa: BLE001
                pass
            await self._close_session(ls, "server shutdown")
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ================================================================ read side (UI)

    def active_session(self, patient_id: str) -> LiveSession | None:
        return self.by_patient.get(patient_id)

    def session_by_id(self, session_id: str) -> LiveSession | None:
        for ls in self.sessions.values():
            if ls.id == session_id:
                return ls
        return None

    def backlog(self, patient_id: str) -> list[str]:
        """Encoded messages a newly connected UI needs: session, ~10 s of signal, 60 s of beats, latest window."""
        ls = self.by_patient.get(patient_id)
        if ls is None:
            return []
        out = [encode(session_message(ls.info)),
               encode({"type": "status", "stream": "connected", "last_sample_time": iso(ls.last_frame_at)})]
        out.extend(text for _, text in ls.signal_backlog)
        out.extend(text for _, text in ls.beat_backlog)
        if ls.window_text:
            out.append(ls.window_text)
        if ls.quality_text:
            out.append(ls.quality_text)
        return out

    def alert_changed(self, alert: dict[str, Any]) -> None:
        """Called after a workflow transition was committed: refresh the dedupe cache and push to live UIs."""
        cache = self.open_alerts.get(alert["patient_id"])
        if cache is not None:
            current = cache.get(alert["rule_id"])
            if current is not None and current["id"] == alert["id"]:
                if alert["state"] == "open":
                    cache[alert["rule_id"]] = alert
                else:
                    del cache[alert["rule_id"]]
        self.hub.publish(alert["patient_id"], {"type": "alert", "alert": alert})

    def stats(self) -> dict[str, Any]:
        return {
            "active_sessions": len(self.sessions),
            "total_sessions": self.total_sessions,
            "total_frames": self.total_frames,
            "rejected_connections": self.rejected,
            "latency_ms": self.latency.summary(),
        }
