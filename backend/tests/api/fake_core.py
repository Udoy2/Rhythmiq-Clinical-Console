"""A tiny deterministic stand-in for ``cardiosense.core.StreamProcessor`` (CONTRACT §3 interface).

Used for ingest-mechanics tests (ordering, gaps, alert de-duplication) where exact, predictable output
matters, and as the fallback when the real core cannot be imported. Behaviour:

* signal: samples converted to mV and passed through;
* one beat per second of signal (label "V", confidence 0.9), emitted one beat later;
* one rhythm window per 10 s with a fixed amber/orange risk;
* an alert candidate for rule ``V_RUN`` when the second window closes and again at every later window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

RULE_VERSION = "rules-fake-0.1"
MODEL_VERSION = "fake-model-0.1"


@dataclass
class PipelineOutput:
    signal: dict | None = None
    beats: list = field(default_factory=list)
    windows: list = field(default_factory=list)
    alerts: list = field(default_factory=list)
    quality: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    tasks: list = field(default_factory=list)


class FakeStreamProcessor:
    def __init__(self, fs: int, classifier: Any = None, adc_zero: float | None = None, adc_gain: float | None = None,
                 rules_config: dict | None = None) -> None:
        self.fs = int(fs)
        self.zero, self.gain = float(adc_zero or 0.0), float(adc_gain or 1.0)
        self.model_version = MODEL_VERSION
        self.rule_version = RULE_VERSION
        self.rules_config = rules_config
        self._next: int | None = None
        self._beats_emitted = 0
        self._windows = 0
        self._lead_off = False
        self._truth: dict[int, str] = {}

    def push(self, samples: Any, t0: int, lead_off: bool = False, annotations: list[dict] | None = None) -> PipelineOutput:
        out = PipelineOutput()
        x = np.asarray(samples, dtype=np.float64)
        if self._next is not None and t0 > self._next:
            out.gaps.append({"at_sample": self._next, "missing": t0 - self._next})
            out.quality.append({"state": "gap", "detail": "samples missing", "sqi": 0.5})
        if lead_off != self._lead_off:
            self._lead_off = lead_off
            out.quality.append({"state": "lead_off" if lead_off else "ok", "detail": "electrodes", "sqi": 0.0 if lead_off else 1.0})
            if lead_off:
                out.tasks.append({"rule_id": "LEAD_OFF", "title": "Electrodes disconnected", "detail": "lead-off flag"})
        for a in annotations or []:
            self._truth[int(a["sample"]) // self.fs] = "V" if a.get("symbol") == "V" else "N"
        self._next = t0 + x.size
        mv = (x - self.zero) / self.gain
        out.signal = {"t0": int(t0), "fs": self.fs, "mv": np.round(mv, 4).tolist(), "lead_off": bool(lead_off)}
        self._advance(out, final=False)
        return out

    def flush(self) -> PipelineOutput:
        out = PipelineOutput()
        self._advance(out, final=True)
        return out

    def _advance(self, out: PipelineOutput, final: bool) -> None:
        if self._next is None:
            return
        seconds = self._next // self.fs
        # beat k sits at k*fs + fs//2 and is released one second (one beat) later
        while self._beats_emitted < (seconds if final else seconds - 1):
            k = self._beats_emitted
            sample = k * self.fs + self.fs // 2
            out.beats.append({"sample": sample, "time_s": round(sample / self.fs, 3), "label": "V", "confidence": 0.9,
                              "probs": {"N": 0.05, "S": 0.0, "V": 0.9, "F": 0.05}, "model_version": MODEL_VERSION,
                              "truth": self._truth.get(k)})
            self._beats_emitted += 1
        while (self._windows + 1) * 10 <= seconds:
            k = self._windows
            self._windows += 1
            tier = "orange" if k >= 1 else "amber"
            finding = {"rule_id": "V_RUN", "label": "V run (non-sustained)", "severity": tier, "start_s": k * 10.0,
                       "end_s": k * 10.0 + 10, "value": 10.0, "detail": "10 consecutive V beats"}
            window = {
                "start_s": k * 10.0, "end_s": k * 10.0 + 10, "hr_bpm": 60.0, "findings": [finding],
                "s_burden_pct": 0.0, "v_burden_pct": 100.0, "sqi": 0.95, "sdnn_ms": 5.0, "rmssd_ms": 4.0,
                "beat_counts": {"N": 0, "S": 0, "V": 10, "F": 0, "Unknown": 0}, "rule_version": RULE_VERSION,
                "risk": {"score": 60 if k >= 1 else 30, "tier": tier,
                         "factors": [{"text": "10 consecutive V beats", "points": 60, "rule_id": "V_RUN"}]},
            }
            out.windows.append(window)
            if k >= 1:  # "confirmed in 2 consecutive windows"
                out.alerts.append({
                    "rule_id": "V_RUN", "tier": "orange", "title": "Non-sustained V run",
                    "detail": "10 consecutive V beats",
                    "evidence": {"start_s": k * 10.0, "end_s": k * 10.0 + 10, "fs": self.fs,
                                 "strip_mv": [0.0] * (self.fs * 10), "beats": [], "window": window,
                                 "rule_version": RULE_VERSION, "model_version": MODEL_VERSION},
                })

    def stats(self) -> dict:
        return {"beats": self._beats_emitted, "windows": self._windows}
