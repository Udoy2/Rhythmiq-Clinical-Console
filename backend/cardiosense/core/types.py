"""Shared constants and plain data shapes of the pipeline core (CONTRACT §2.2 / §3)."""
from __future__ import annotations

from dataclasses import dataclass, field

LABELS = ("N", "S", "V", "F", "Unknown")
TIERS = ("green", "amber", "orange", "red")
TIER_RANK = {t: i for i, t in enumerate(TIERS)}

# clinical rule ids
BRADY, BRADY_SEVERE = "BRADY", "BRADY_SEVERE"
TACHY, TACHY_SEVERE = "TACHY", "TACHY_SEVERE"
PAUSE, PAUSE_CRITICAL = "PAUSE", "PAUSE_CRITICAL"
V_COUPLET, V_RUN, V_SUSTAINED = "V_COUPLET", "V_RUN", "V_SUSTAINED"
BIGEMINY, TRIGEMINY = "BIGEMINY", "TRIGEMINY"
S_RUN, S_BURDEN, V_BURDEN, IRREGULAR = "S_RUN", "S_BURDEN", "V_BURDEN", "IRREGULAR"
CLINICAL_RULES = (BRADY, BRADY_SEVERE, TACHY, TACHY_SEVERE, PAUSE, PAUSE_CRITICAL, V_COUPLET, V_RUN,
                  V_SUSTAINED, BIGEMINY, TRIGEMINY, S_RUN, S_BURDEN, V_BURDEN, IRREGULAR)
# device tasks (never clinical alerts)
LEAD_OFF, LOW_SQI = "LEAD_OFF", "LOW_SQI"

RULE_LABELS = {
    BRADY: "Bradycardia", BRADY_SEVERE: "Severe bradycardia",
    TACHY: "Tachycardia", TACHY_SEVERE: "Severe tachycardia",
    PAUSE: "Pause", PAUSE_CRITICAL: "Critical pause",
    V_COUPLET: "V couplet", V_RUN: "V run (non-sustained)", V_SUSTAINED: "Sustained V pattern",
    BIGEMINY: "Bigeminy", TRIGEMINY: "Trigeminy", S_RUN: "S run",
    S_BURDEN: "S ectopic burden", V_BURDEN: "V ectopic burden",
    IRREGULAR: "Irregular rhythm — review",
}

# MIT-BIH annotation symbol -> AAMI class (None = not scored: paced, unclassifiable, ...)
AAMI = {**dict.fromkeys("NLRej", "N"), **dict.fromkeys("AaJS", "S"), **dict.fromkeys("VE", "V"), "F": "F"}
MIT_BEAT_SYMBOLS = frozenset("NLRBAaJSVrFejnE/fQ?")


def max_tier(*tiers: str) -> str:
    return max(tiers, key=TIER_RANK.__getitem__) if tiers else "green"


def min_tier(a: str, b: str) -> str:
    return a if TIER_RANK[a] <= TIER_RANK[b] else b


@dataclass
class Finding:
    rule_id: str
    severity: str
    start_s: float
    end_s: float
    value: float
    detail: str
    label: str = ""

    def to_dict(self) -> dict:
        return {"rule_id": self.rule_id, "label": self.label or RULE_LABELS.get(self.rule_id, self.rule_id),
                "severity": self.severity, "start_s": round(self.start_s, 3), "end_s": round(self.end_s, 3),
                "value": round(float(self.value), 3), "detail": self.detail}


@dataclass
class BeatEvent:
    """A finalised beat. `sample`/`time_s` are in the device sample rate / timebase."""
    sample: int
    time_s: float
    label: str
    confidence: float
    probs: dict
    model_version: str | None
    truth: str | None = None
    seg: int = 0            # contiguous-signal segment (changes at every gap)
    latency_s: float = 0.0  # signal time between the R-peak and emission

    def to_dict(self) -> dict:
        return {"sample": self.sample, "time_s": round(self.time_s, 3), "label": self.label,
                "confidence": round(self.confidence, 4),
                "probs": {k: round(v, 4) for k, v in self.probs.items()},
                "model_version": self.model_version, "truth": self.truth}


@dataclass
class PipelineOutput:
    """Result of one `push`/`flush`; every field is plain JSON-serialisable data."""
    signal: dict | None = None
    beats: list = field(default_factory=list)
    windows: list = field(default_factory=list)
    alerts: list = field(default_factory=list)
    quality: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    tasks: list = field(default_factory=list)   # device tasks (LEAD_OFF / LOW_SQI) — additive to CONTRACT §3

    def to_dict(self) -> dict:
        return {"signal": self.signal, "beats": self.beats, "windows": self.windows, "alerts": self.alerts,
                "quality": self.quality, "gaps": self.gaps, "tasks": self.tasks}
