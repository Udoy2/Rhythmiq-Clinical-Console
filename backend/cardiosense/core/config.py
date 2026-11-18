"""Versioned thresholds of the rules / quality / risk / alert engines (BRD §8.4, §11).

Defaults are the R1 values; an organisation may override any field through the
`rules_config` dict given to `StreamProcessor` (unknown keys are ignored, dict
fields are merged key by key).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields

RULE_VERSION = "rules-1.0.0"


@dataclass
class RulesConfig:
    window_s: float = 10.0
    # --- rate (median RR over the window, N and S beats) ---
    brady_bpm: float = 50.0
    brady_severe_bpm: float = 40.0
    tachy_bpm: float = 100.0
    tachy_severe_bpm: float = 150.0
    hr_min_intervals: int = 3
    # --- pauses ---
    pause_s: float = 2.0
    pause_critical_s: float = 3.0
    # a pause is only accepted if the signal between the two beats holds no QRS-sized deflection (fastest
    # 80 ms swing <= ratio x median QRS amplitude of the last beats); otherwise it is a missed beat. 0 = off
    pause_activity_ratio: float = 0.35
    rr_verify_min_s: float = 0.7         # the same check screens every RR interval at least this long: an
                                         # interval hiding a missed beat is left out of rate, pause and NN maths
    # --- ectopic patterns ---
    v_run_min_beats: int = 3
    v_sustained_s: float = 30.0
    v_sustained_bpm: float = 100.0
    geminy_min_cycles: int = 6
    s_run_min_beats: int = 3
    s_run_bpm: float = 100.0
    # --- burden (% of labelled beats; evaluated on the 1 h window) ---
    burden_amber_pct: float = 1.0
    burden_orange_pct: float = 10.0      # V only; S never exceeds amber
    burden_min_beats: int = 30           # labelled beats needed before a burden is reported ...
    burden_min_ectopics: int = 3         # ... and ectopic beats of that class (one beat is not a burden)
    burden_min_span_s: float = 60.0
    # --- irregularity / HRV (NN intervals, ectopics excluded) ---
    irregular_window_s: float = 60.0
    irregular_ratio: float = 0.15        # RMSSD / mean RR
    irregular_min_intervals: int = 30
    irregular_orange_windows: int = 2    # consecutive windows before the finding becomes orange
    hrv_window_s: float = 300.0
    hrv_min_intervals: int = 20
    nn_min_s: float = 0.3                # NN intervals outside this range are artefacts / pauses
    nn_max_s: float = 2.0
    # --- signal quality ---
    sqi_low: float = 0.5                 # below: clinical alerts suppressed, LOW_SQI device task
    lead_off_frac: float = 0.05          # fraction of a window flagged lead-off that makes it a LEAD_OFF window
    flat_ptp_mv: float = 0.008           # peak-to-peak below this over a 2 s block = flat line
    hf_ratio_ok: float = 0.5             # rms(>45 Hz) / rms(0.5-40 Hz): no penalty below, full penalty above _bad
    hf_ratio_bad: float = 1.5
    bw_ratio_ok: float = 1.5             # baseline excursion / QRS amplitude
    bw_ratio_bad: float = 5.0
    rr_implausible_s: float = 0.25
    sqi_weights: dict = field(default_factory=lambda: {
        "hf": 0.6, "baseline": 0.5, "unknown": 0.8, "rr": 0.6, "saturation": 1.0, "flat": 1.0})
    # --- risk ---
    risk_points: dict = field(default_factory=lambda: {
        "BRADY": 30, "BRADY_SEVERE": 85, "TACHY": 30, "TACHY_SEVERE": 85, "PAUSE": 60, "PAUSE_CRITICAL": 90,
        "V_COUPLET": 30, "V_RUN": 60, "V_SUSTAINED": 95, "BIGEMINY": 35, "TRIGEMINY": 32, "S_RUN": 30,
        "S_BURDEN": 25, "V_BURDEN": 30, "IRREGULAR": 30})
    risk_tier_floor: dict = field(default_factory=lambda: {"green": 0, "amber": 25, "orange": 50, "red": 80})
    risk_secondary_weight: float = 0.3   # share of every finding after the strongest one
    risk_trend_points: int = 5
    risk_decay_clean_windows: int = 3    # clean windows needed before the tier steps down one level
    # --- alerts ---
    alert_confirm_windows: int = 2       # orange/red: consecutive windows with the finding
    alert_cooldown_s: dict = field(default_factory=lambda: {"amber": 3600.0, "orange": 900.0, "red": 300.0})
    alert_episode_clear_windows: int = 6  # windows without the finding that end an episode

    @classmethod
    def from_dict(cls, d: dict | None) -> "RulesConfig":
        cfg = cls()
        for f in fields(cls):
            if not d or f.name not in d or d[f.name] is None:
                continue
            cur, new = getattr(cfg, f.name), d[f.name]
            if isinstance(cur, dict):
                if not isinstance(new, dict):
                    raise ValueError(f"rules_config[{f.name!r}] must be a dict")
                cur.update(new)
            else:
                setattr(cfg, f.name, type(cur)(new))
        return cfg

    def to_dict(self) -> dict:
        return asdict(self)

    def version(self) -> str:
        """`RULE_VERSION`, suffixed with a hash of the thresholds when they differ from the defaults."""
        mine, default = self.to_dict(), RulesConfig().to_dict()
        if mine == default:
            return RULE_VERSION
        h = hashlib.sha256(json.dumps(mine, sort_keys=True).encode()).hexdigest()[:8]
        return f"{RULE_VERSION}+{h}"
