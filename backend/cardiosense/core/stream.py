"""`StreamProcessor`: frames of raw samples in, display signal / beats / windows / alerts out (CONTRACT §3)."""
from __future__ import annotations

from collections import deque
from math import gcd
from time import perf_counter

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy.signal import resample_poly

from .alerts import AlertEngine
from .beats import BeatStream
from .config import RulesConfig
from .quality import QualityMonitor, display_filter
from .risk import RiskEngine
from .rules import RhythmEngine
from .types import AAMI, LEAD_OFF, LOW_SQI, MIT_BEAT_SYMBOLS, PAUSE_CRITICAL, BeatEvent, Finding, PipelineOutput

TRUTH_TOL_S = 0.150
RING_S = 45.0               # display signal kept for evidence strips
STRIP_S = 10.0
STRIP_MAX_FS = 250
TEMPLATE_KEEP_GAP_S = 30.0  # the beat template survives gaps shorter than this; RR history never does
TASK_TITLES = {LEAD_OFF: "Check electrodes — lead-off", LOW_SQI: "Poor signal quality — check electrodes and cable"}


class _TruthMatcher:
    """Replay ground truth: beat annotations (absolute device sample, raw MIT-BIH symbol) -> AAMI class."""

    def __init__(self, tol: int):
        self.tol = tol
        self._ann: deque = deque()

    def add(self, annotations) -> None:
        for a in annotations:
            sym = a.get("symbol")
            if sym in MIT_BEAT_SYMBOLS:
                self._ann.append((int(a["sample"]), sym))

    def match(self, sample: int) -> tuple[bool, str | None]:
        """(matched, AAMI class or None). Consumes the annotation so it is used for one beat only."""
        ann = self._ann
        while ann and ann[0][0] < sample - self.tol:
            ann.popleft()
        best = None
        for i, (s, _) in enumerate(ann):
            if s > sample + self.tol:
                break
            if best is None or abs(s - sample) < abs(ann[best][0] - sample):
                best = i
        if best is None:
            return False, None
        sym = ann[best][1]
        for _ in range(best + 1):
            ann.popleft()
        return True, AAMI.get(sym)


class StreamProcessor:
    def __init__(self, fs: int, classifier=None, adc_zero: float | None = None, adc_gain: float | None = None,
                 rules_config: dict | RulesConfig | None = None, *, proc_interval_s: float = 0.5,
                 peak_guard_s: float = 0.35, filt_guard_s: float = 0.25, beat_timeout_s: float = 2.5):
        self.fs = int(round(fs))
        if self.fs <= 0:
            raise ValueError("fs must be positive")
        self.cfg = rules_config if isinstance(rules_config, RulesConfig) else RulesConfig.from_dict(rules_config)
        self._zero = float(adc_zero or 0.0)
        self._gain = float(adc_gain or 1.0)
        self.model_loaded = classifier is not None
        self._bs = BeatStream(self.fs, classifier, proc_interval_s, peak_guard_s, filt_guard_s, beat_timeout_s)
        self.model_version = self._bs.model_version
        self.rule_version = self.cfg.version()
        self._engine = RhythmEngine(self.cfg, rules_only=not self.model_loaded, pause_check=self._pause_is_quiet)
        self._risk = RiskEngine(self.cfg)
        self._alerts = AlertEngine(self.cfg)
        self._quality = QualityMonitor(self.fs, self.cfg)
        self._display = display_filter(self.fs)
        self._truth = _TruthMatcher(int(round(TRUTH_TOL_S * self.fs)))

        self._next = None                       # next expected absolute sample index
        self._seg = 0
        self._win = 0                           # next rhythm window to close
        self._ring = np.empty(int((RING_S + 15) * self.fs))
        self._ring_n, self._ring_start = 0, 0
        self._recent: deque = deque()           # beat dicts of the last minute (evidence)
        self._qrs_amp: deque = deque(maxlen=8)  # QRS amplitudes (display signal) of the latest beats
        self._last_window = None
        self._q_state, self._q_sqi = "ok", 1.0
        self._lead_off_now = False
        self._last_lead_off = -1                # absolute index of the last lead-off sample
        self._flat_run = 0                      # samples of flat raw signal up to now
        self._last_sample_value = self._zero
        # stats
        self._n_samples = self._n_windows = self._n_alerts = self._n_gaps = self._n_truth = 0
        self._labels = {k: 0 for k in ("N", "S", "V", "F", "Unknown")}
        self._latency: deque = deque(maxlen=50000)
        self._push_ms: deque = deque(maxlen=50000)

    # ------------------------------------------------------------------ public
    def push(self, samples, t0: int, lead_off: bool = False, annotations: list | None = None) -> PipelineOutput:
        tic = perf_counter()
        out = PipelineOutput()
        x = np.asarray(samples, dtype=np.float64).ravel()
        t0 = int(t0)
        if annotations:
            self._truth.add(annotations)
        if x.size:
            if not np.isfinite(x).all():        # hold the last good value over NaN/inf samples
                idx = np.where(np.isfinite(x), np.arange(x.size), -1)
                np.maximum.accumulate(idx, out=idx)
                x = np.where(idx < 0, self._last_sample_value, x[np.maximum(idx, 0)])
            self._last_sample_value = float(x[-1])
            mv = (x - self._zero) / self._gain
            if self._next is None:
                self._start_segment(t0)
            elif t0 > self._next:
                self._gap(t0, out)
            elif t0 < self._next:               # overlap / duplicate: keep only what is new
                mv = mv[self._next - t0:]
                t0 = self._next
            if mv.size:
                self._ingest(mv, t0, bool(lead_off), out)
        self._push_ms.append((perf_counter() - tic) * 1000.0)
        return out

    def flush(self) -> PipelineOutput:
        """End of stream: emit every remaining beat and close the windows, including a final partial one."""
        out = PipelineOutput()
        if self._next is not None:
            self._advance(out, final=True)
            end_s = self._next / self.fs
            if self._quality.has(self._win) and end_s - self._win * self.cfg.window_s >= 1.0:
                self._close_window(self._win, out, end_s)
                self._win += 1
        return out

    def stats(self) -> dict:
        def pct(v, qs):
            a = np.asarray(v, dtype=float)
            return {name: (round(float(np.percentile(a, q)), 3) if a.size else None) for name, q in qs}
        lat = pct(self._latency, (("median", 50), ("p95", 95), ("max", 100)))
        cpu = pct(self._push_ms, (("p50", 50), ("p95", 95), ("max", 100)))
        cpu["mean"] = round(float(np.mean(self._push_ms)), 3) if self._push_ms else None
        return {"beats": sum(self._labels.values()), "labels": dict(self._labels), "latency_s": lat, "push_ms": cpu,
                "samples": self._n_samples, "signal_s": round(self._n_samples / self.fs, 3),
                "windows": self._n_windows, "alerts": self._n_alerts, "gaps": self._n_gaps,
                "truth_matched": self._n_truth, "quality": self._q_state, "risk_tier": self._risk.tier,
                "model_loaded": self.model_loaded, "model_version": self.model_version,
                "rule_version": self.rule_version}

    # ---------------------------------------------------------------- ingest
    def _start_segment(self, t0: int, keep_template: bool = False) -> None:
        self._bs.start_segment(t0, self._seg, keep_template)
        self._display.reset()
        self._quality.reset_filters()
        self._ring_n, self._ring_start = 0, t0
        self._qrs_amp.clear()

    def _gap(self, t0: int, out: PipelineOutput) -> None:
        missing = t0 - self._next
        self._advance(out, final=True)          # drain the segment that just ended; nothing is interpolated
        out.gaps.append({"at_sample": self._next, "missing": missing})
        self._n_gaps += 1
        self._seg += 1
        self._start_segment(t0, keep_template=missing / self.fs <= TEMPLATE_KEEP_GAP_S)
        self._set_quality("gap", f"{missing / self.fs:.1f} s of signal missing at {self._next / self.fs:.1f} s "
                                 f"(not interpolated)", self._q_sqi, out)

    def _ingest(self, mv: np.ndarray, t0: int, lead_off: bool, out: PipelineOutput) -> None:
        n = len(mv)
        disp = self._display(mv)
        out.signal = {"t0": t0, "fs": self.fs, "mv": np.round(disp, 4).tolist(), "lead_off": lead_off}
        self._quality.add(t0, mv, disp, lead_off)
        tail = disp[-len(self._ring):]          # (only an absurdly large frame is longer than the ring)
        if self._ring_n + len(tail) > len(self._ring):
            drop = min(self._ring_n, max(self._ring_n + len(tail) - len(self._ring), 10 * self.fs))
            self._ring[:self._ring_n - drop] = self._ring[drop:self._ring_n]
            self._ring_n -= drop
            self._ring_start += drop
        if self._ring_n == 0:
            self._ring_start = t0 + n - len(tail)
        self._ring[self._ring_n:self._ring_n + len(tail)] = tail
        self._ring_n += len(tail)

        blk = max(1, self.fs // 10)             # run of dead-flat raw signal (cable pulled without a lead-off flag)
        for i in range(0, n, blk):
            c = mv[i:i + blk]
            self._flat_run = self._flat_run + len(c) if np.ptp(c) <= self.cfg.flat_ptp_mv else 0
        if lead_off:
            self._last_lead_off = t0 + n - 1
        if lead_off != self._lead_off_now:
            self._lead_off_now = lead_off
            if lead_off:
                self._set_quality("lead_off", "Electrode lead-off reported by the device — check electrodes", 0.0, out)
            else:
                self._set_quality("low_sqi", "Electrodes reconnected — waiting for a clean window", self._q_sqi, out)

        self._next = t0 + n
        self._n_samples += n
        step = self._bs.proc_step
        for i in range(0, n, step):
            self._bs.append(mv[i:i + step])
            if self._bs.due:
                self._advance(out)

    # ------------------------------------------------------- beats and windows
    def _advance(self, out: PipelineOutput, final: bool = False) -> None:
        for ev in self._bs.process(final):
            self._emit_beat(ev, out)
        now_s = self._bs.frontier / self.fs
        pause = self._engine.ongoing_pause(now_s, self._seg)
        if pause is not None and pause.rule_id == PAUSE_CRITICAL:
            self._immediate(pause, now_s, out)
        self._close_windows(now_s, out)

    def _emit_beat(self, ev: BeatEvent, out: PipelineOutput) -> None:
        matched, ev.truth = self._truth.match(ev.sample)
        self._n_truth += matched
        self._close_windows(ev.time_s, out)     # windows ending before this beat are complete
        for pause in self._engine.add_beat(ev.time_s, ev.label, ev.seg):
            self._immediate(pause, ev.time_s, out)
        j = ev.sample - self._ring_start
        qrs = self._ring[max(0, j - int(0.04 * self.fs)):min(self._ring_n, j + int(0.08 * self.fs) + 1)]
        if qrs.size:
            self._qrs_amp.append(float(np.ptp(qrs)))
        d = ev.to_dict()
        self._recent.append(d)
        while self._recent and self._recent[0]["time_s"] < ev.time_s - 60.0:
            self._recent.popleft()
        self._labels[ev.label] += 1
        self._latency.append(ev.latency_s)
        out.beats.append(d)

    def _close_windows(self, upto_s: float, out: PipelineOutput) -> None:
        W = self.cfg.window_s
        while (self._win + 1) * W <= upto_s + 1e-9:
            k = self._win
            if self._quality.has(k):
                self._close_window(k, out)
                self._win += 1
            else:                               # no signal in this window (inside a gap): skip ahead
                first, limit = self._quality.first_window(), int((upto_s + 1e-9) // W)
                self._win = min(first, limit) if first is not None and first > k else limit

    def _close_window(self, k: int, out: PipelineOutput, end_s: float | None = None) -> None:
        W = self.cfg.window_s
        start, end = k * W, (k + 1) * W if end_s is None else end_s
        st = self._engine.window_stats(start, end)
        q = self._quality.close(k, st["n_beats"], st["n_unknown"], st["rr"], use_unknown=self.model_loaded,
                                rr_bad=st["rr_bad"])
        if q is None:
            return
        res = self._engine.close_window(start, end, q.sqi, self._seg)
        window = {**res.window, "risk": self._risk.update(res.findings, res.burden, q.sqi, q.suppressed, end)}
        out.windows.append(window)
        self._last_window = window
        self._n_windows += 1
        for d in self._alerts.on_window(res.findings, end, q.suppressed):
            out.alerts.append(d.candidate(self._evidence(d.finding, window)))
            self._n_alerts += 1
        if self._lead_off_now:
            self._q_sqi = q.sqi                 # still off: keep the lead_off state raised by the device flag
        else:
            self._set_quality(q.state, q.detail, q.sqi, out)

    def _immediate(self, f: Finding, now_s: float, out: PipelineOutput) -> None:
        fs = self.fs
        i0 = max(0, int(f.start_s * fs) - self._ring_start)
        i1 = min(self._ring_n, int(f.end_s * fs) - self._ring_start)
        suppressed = (self._q_state in ("lead_off", "low_sqi") or self._lead_off_now
                      or self._last_lead_off >= int(f.start_s * fs)
                      or i1 - i0 < fs or self._flat_run >= fs)
        for d in self._alerts.on_immediate(Finding(**vars(f)), now_s, suppressed):
            out.alerts.append(d.candidate(self._evidence(d.finding, self._last_window)))
            self._n_alerts += 1

    def _pause_is_quiet(self, start_s: float, end_s: float) -> bool:
        """Pause verification: no QRS-sized deflection between the two beats (else the detector missed a beat)."""
        ratio, fs = self.cfg.pause_activity_ratio, self.fs
        if ratio <= 0 or len(self._qrs_amp) < 3:
            return True
        w = max(2, int(0.08 * fs))
        i0 = max(0, int((start_s + 0.35) * fs) - self._ring_start)
        i1 = min(self._ring_n, int((end_s - 0.15) * fs) - self._ring_start)
        if i1 - i0 <= w:
            return True
        v = sliding_window_view(self._ring[i0:i1], w)[::max(1, w // 4)]
        return float((v.max(1) - v.min(1)).max()) <= ratio * float(np.median(self._qrs_amp))

    def _set_quality(self, state: str, detail: str, sqi: float, out: PipelineOutput) -> None:
        prev, self._q_sqi = self._q_state, sqi
        if state == prev:
            return
        self._q_state = state
        out.quality.append({"state": state, "detail": detail, "sqi": sqi})
        if state in ("lead_off", "low_sqi") and prev not in ("lead_off", "low_sqi"):
            rule = LEAD_OFF if state == "lead_off" else LOW_SQI
            out.tasks.append({"rule_id": rule, "kind": "device_task", "title": TASK_TITLES[rule], "detail": detail,
                              "sqi": sqi, "at_s": round(self._next / self.fs, 3) if self._next else 0.0})

    # ---------------------------------------------------------------- evidence
    def _evidence(self, f: Finding, window: dict | None) -> dict:
        """Snapshot stored with an alert: <= 10 s display strip around the finding, its beats, the window."""
        fs = self.fs
        ring0, ring1 = self._ring_start / fs, (self._ring_start + self._ring_n) / fs
        if f.end_s - f.start_s <= STRIP_S - 2.0:
            s = (f.start_s + f.end_s) / 2.0 - STRIP_S / 2.0
        else:
            s = f.end_s - (STRIP_S - 1.0)       # long findings: the most recent stretch
        s = max(ring0, min(s, ring1 - STRIP_S))
        e = min(ring1, s + STRIP_S)
        i0, i1 = int(round(s * fs)) - self._ring_start, int(round(e * fs)) - self._ring_start
        strip, strip_fs = self._ring[i0:i1], fs
        if fs > STRIP_MAX_FS and len(strip) > 1:
            g = gcd(fs, STRIP_MAX_FS)
            strip, strip_fs = resample_poly(strip, STRIP_MAX_FS // g, fs // g), STRIP_MAX_FS
        return {"start_s": round(s, 3), "end_s": round(e, 3), "fs": strip_fs,
                "strip_mv": np.round(strip, 3).tolist(),
                "beats": [b for b in self._recent if s <= b["time_s"] <= e],
                "window": window, "finding": f.to_dict(),
                "rule_version": self.rule_version, "model_version": self.model_version}
