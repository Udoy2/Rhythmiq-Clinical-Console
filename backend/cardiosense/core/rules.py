"""Deterministic rhythm rules engine (BRD §8.4).

Beats are fed in order with `add_beat`; pattern detectors (pause, V couplet /
run / sustained, S run, bigeminy, trigeminy) are incremental state machines
over the beat stream, independent of the 10 s windows. Each occurrence is one
*episode* (one `Finding` object that grows while the pattern continues), so a
run that crosses a window boundary is neither missed nor counted twice; a
window reports every episode that overlaps it.

Unknown beats never count towards a pattern or a burden and break any run in
progress. A change of segment (signal gap) breaks everything: no RR interval,
run or pause is ever measured across a gap.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from .config import RulesConfig
from .types import (BIGEMINY, BRADY, BRADY_SEVERE, IRREGULAR, LABELS, PAUSE, PAUSE_CRITICAL, S_BURDEN, S_RUN,
                    TACHY, TACHY_SEVERE, TIER_RANK, TRIGEMINY, V_BURDEN, V_COUPLET, V_RUN, V_SUSTAINED, Finding)

# BRD §11 tier mapping. V_BURDEN and IRREGULAR start amber and become orange (see RulesConfig).
RULE_TIER = {
    BRADY: "amber", BRADY_SEVERE: "red", TACHY: "amber", TACHY_SEVERE: "red",
    PAUSE: "orange", PAUSE_CRITICAL: "red",
    V_COUPLET: "amber", V_RUN: "orange", V_SUSTAINED: "red",
    BIGEMINY: "amber", TRIGEMINY: "amber", S_RUN: "amber", S_BURDEN: "amber",
    V_BURDEN: "amber", IRREGULAR: "amber",
}
HISTORY_S = 3600.0


@dataclass
class WindowResult:
    window: dict            # §2.2 window without "risk" / "id"
    findings: list          # list[Finding]
    burden: dict            # {"s": {"1min","1h","session"}, "v": {...}}, percent of labelled beats


def _pct(part: int, counts: dict) -> float:
    total = counts["N"] + counts["S"] + counts["V"] + counts["F"]
    return 100.0 * part / total if total else 0.0


def _rmssd(rr: list, idx: list) -> tuple[float, int]:
    """RMSSD over successive intervals only (the beat indices must be consecutive)."""
    d = [b - a for a, b, i, j in zip(rr, rr[1:], idx, idx[1:]) if j == i + 1]
    return (float(np.sqrt(np.mean(np.square(d)))), len(d)) if d else (0.0, 0)


class RhythmEngine:
    def __init__(self, cfg: RulesConfig | None = None, rules_only: bool = False, pause_check=None):
        self.cfg = cfg or RulesConfig()
        self.rules_only = rules_only          # no classifier: every beat is Unknown, rate rules use all beats
        # optional `pause_check(start_s, end_s) -> bool`: True if the signal really is quiet in between
        self.pause_check = pause_check
        self._rr_bad: deque = deque()         # end times of RR intervals rejected as hiding a missed beat
        self.rule_version = self.cfg.version()
        self._beats: deque = deque()          # (t, label, seg, rr_prev | None, prev_label)
        self._nn: deque = deque()             # (t, rr, beat_index) normal-to-normal intervals
        self._prev = None                     # (t, label, seg)
        self._idx = 0
        self._first_t = None
        self.session_counts = {k: 0 for k in LABELS}
        self._hour_counts = {k: 0 for k in LABELS}
        self._episodes: list[Finding] = []
        self._irr_streak = 0
        self._reset_patterns()

    # ------------------------------------------------------------- beat stream
    def _reset_patterns(self) -> None:
        self._v, self._v_ep = [], None
        self._s, self._s_ep = [], None
        self._nonv, self._after_v, self._last_nonv = 0, False, []
        self._bi = self._tri = None           # [start_s, cycles, Finding | None]
        self._open_pause = None

    def _episode(self, ep: Finding | None, rule: str, start: float, end: float, value: float, detail: str) -> Finding:
        if ep is None:
            ep = Finding(rule, RULE_TIER[rule], start, end, value, detail)
            self._episodes.append(ep)
        else:
            ep.rule_id, ep.severity, ep.end_s, ep.value, ep.detail = rule, RULE_TIER[rule], end, value, detail
        return ep

    def _pause(self, start: float, end: float) -> Finding:
        dur = end - start
        rule = PAUSE_CRITICAL if dur > self.cfg.pause_critical_s else PAUSE
        ep = self._open_pause if self._open_pause is not None and self._open_pause.start_s == start else None
        return self._episode(ep, rule, start, end, dur, f"No beat for {dur:.1f} s")

    def add_beat(self, t: float, label: str, seg: int = 0) -> list[Finding]:
        """Feed the next beat (time in s, strictly increasing). Returns critical pauses completed by this beat."""
        cfg, prev = self.cfg, self._prev
        contiguous = prev is not None and prev[2] == seg
        if not contiguous:
            self._reset_patterns()
        rr = t - prev[0] if contiguous else None
        self._idx += 1
        if self._first_t is None:
            self._first_t = t
        # an interval with QRS-sized activity inside is a detector miss, not a real RR interval
        verified = not (rr is not None and rr >= cfg.rr_verify_min_s and self.pause_check is not None
                        and not self.pause_check(prev[0], t))
        self._beats.append((t, label, seg, rr, prev[1] if contiguous else None, verified))
        self.session_counts[label] += 1
        self._hour_counts[label] += 1

        critical = []
        if not verified:
            self._rr_bad.append(t)
            if self._open_pause is not None:           # reported while ongoing, refuted now
                self._episodes = [e for e in self._episodes if e is not self._open_pause]
        elif rr is not None and rr > cfg.pause_s:
            ep = self._pause(prev[0], t)
            if ep.rule_id == PAUSE_CRITICAL:
                critical.append(ep)
        self._open_pause = None
        if rr is not None and verified and label == "N" and prev[1] == "N" and cfg.nn_min_s <= rr <= cfg.nn_max_s:
            self._nn.append((t, rr, self._idx))
        self._track_v(t, label)
        self._track_s(t, label)
        self._track_geminy(t, label)
        self._prev = (t, label, seg)
        return critical

    def ongoing_pause(self, now_s: float, seg: int) -> Finding | None:
        """Pause still in progress: every beat before `now_s` is known and the last one is > pause_s old."""
        p = self._prev
        if p is None or p[2] != seg or now_s - p[0] <= self.cfg.pause_s:
            return None
        if self.pause_check is not None and not self.pause_check(p[0], now_s):
            return None
        self._open_pause = self._pause(p[0], now_s)
        self._open_pause.detail += " (ongoing)"
        return self._open_pause

    def _track_v(self, t: float, label: str) -> None:
        cfg = self.cfg
        if label != "V":
            self._v, self._v_ep = [], None
            return
        self._v.append(t)
        n = len(self._v)
        if n < 2:
            return
        dur = t - self._v[0]
        rate = 60.0 * (n - 1) / dur
        if n < cfg.v_run_min_beats:
            rule, detail = V_COUPLET, f"{n} consecutive V beats"
        elif dur >= cfg.v_sustained_s and rate > cfg.v_sustained_bpm:
            rule, detail = V_SUSTAINED, f"{n} consecutive V beats for {dur:.0f} s at {rate:.0f} bpm"
        else:
            rule, detail = V_RUN, f"{n} consecutive V beats over {dur:.1f} s at {rate:.0f} bpm"
        self._v_ep = self._episode(self._v_ep, rule, self._v[0], t, n, detail)

    def _track_s(self, t: float, label: str) -> None:
        cfg = self.cfg
        if label != "S":
            self._s, self._s_ep = [], None
            return
        self._s.append(t)
        n = len(self._s)
        if n < max(2, cfg.s_run_min_beats):
            return
        rate = 60.0 * (n - 1) / (t - self._s[0])
        if rate > cfg.s_run_bpm:
            self._s_ep = self._episode(self._s_ep, S_RUN, self._s[0], t, n,
                                       f"{n} consecutive S beats at {rate:.0f} bpm")

    def _track_geminy(self, t: float, label: str) -> None:
        if label == "Unknown":
            self._nonv, self._after_v, self._last_nonv, self._bi, self._tri = 0, False, [], None, None
            return
        if label != "V":
            self._nonv += 1
            self._last_nonv = (self._last_nonv + [t])[-2:]
            if self._nonv >= 2:
                self._bi = None
            if self._nonv >= 3:
                self._tri = None
            return
        g = self._nonv                      # normal beats since the previous V (or since the last break)
        self._bi = self._geminy_step(self._bi, BIGEMINY, 1, g, t)
        self._tri = self._geminy_step(self._tri, TRIGEMINY, 2, g, t)
        self._nonv, self._after_v, self._last_nonv = 0, True, []

    def _geminy_step(self, st, rule: str, k: int, g: int, t: float):
        """`k` normal beats then a V is one cycle; cycles must follow each other without a break."""
        if g < k:
            return None
        if g == k and self._after_v and st is not None:
            st[1] += 1
        else:
            st = [self._last_nonv[-k], 1, None]
        if st[1] >= self.cfg.geminy_min_cycles:
            every = "2nd" if k == 1 else "3rd"
            st[2] = self._episode(st[2], rule, st[0], t, st[1], f"V every {every} beat for {st[1]} cycles")
        return st

    # ----------------------------------------------------------------- windows
    def window_stats(self, start: float, end: float) -> dict:
        """Beat counts and RR intervals of [start, end) — input to the SQI."""
        counts = {k: 0 for k in LABELS}
        rr = []
        for t, label, _, r, _, _ in reversed(self._beats):
            if t < start:
                break
            if t < end:
                counts[label] += 1
                if r is not None:
                    rr.append(r)
        while self._rr_bad and self._rr_bad[0] < start:
            self._rr_bad.popleft()
        return {"counts": counts, "n_beats": sum(counts.values()), "n_unknown": counts["Unknown"], "rr": rr,
                "rr_bad": sum(1 for t in self._rr_bad if t < end)}

    def close_window(self, start: float, end: float, sqi: float = 1.0, seg: int | None = None) -> WindowResult:
        """Evaluate [start, end). Call once every beat before `end` has been added; pass `seg` (the segment
        the signal is in at `end`) to let a pause that is still in progress at the window end be reported."""
        cfg = self.cfg
        if seg is not None:
            self.ongoing_pause(end, seg)
        while self._beats and self._beats[0][0] < end - HISTORY_S:
            self._hour_counts[self._beats.popleft()[1]] -= 1
        while self._nn and self._nn[0][0] < end - max(cfg.hrv_window_s, cfg.irregular_window_s):
            self._nn.popleft()

        counts = {k: 0 for k in LABELS}
        minute = {k: 0 for k in LABELS}
        hour = dict(self._hour_counts)
        rr_ns, rr_labelled, rr_all = [], [], []
        for t, label, _, rr, prev_label, verified in reversed(self._beats):
            if t >= end:
                hour[label] -= 1
                continue
            if t < end - 60.0:
                break
            minute[label] += 1
            if t >= start:
                counts[label] += 1
                if rr is not None and verified:
                    rr_all.append(rr)
                    if label != "Unknown" and prev_label != "Unknown":
                        rr_labelled.append(rr)
                        if label in "NS" and prev_label in "NS":
                            rr_ns.append(rr)

        findings: list[Finding] = []
        # --- heart rate ---
        if self.rules_only:
            rr_hr, basis = rr_all, "all detected beats"
        elif len(rr_ns) >= cfg.hr_min_intervals:
            rr_hr, basis = rr_ns, "N and S beats"
        else:
            rr_hr, basis = rr_labelled, "all labelled beats"
        hr = 60.0 / float(np.median(rr_hr)) if len(rr_hr) >= cfg.hr_min_intervals else None
        if hr is not None:
            detail = f"Heart rate {hr:.0f} bpm (median RR over {end - start:.0f} s, {basis})"
            rule = (BRADY_SEVERE if hr < cfg.brady_severe_bpm else BRADY if hr < cfg.brady_bpm else
                    TACHY_SEVERE if hr > cfg.tachy_severe_bpm else TACHY if hr > cfg.tachy_bpm else None)
            if rule:
                findings.append(Finding(rule, RULE_TIER[rule], start, end, hr, detail))

        # --- pattern episodes overlapping this window ---
        live = {id(e) for e in (self._v_ep, self._s_ep, self._open_pause,
                                self._bi and self._bi[2], self._tri and self._tri[2]) if e is not None}
        findings += [Finding(**vars(e)) for e in self._episodes if e.end_s >= start and e.start_s < end]
        self._episodes = [e for e in self._episodes if e.end_s >= end or id(e) in live]

        # --- ectopic burden ---
        span = min(HISTORY_S, end - self._first_t) if self._first_t is not None else 0.0
        burden = {}
        for key, rule in (("S", S_BURDEN), ("V", V_BURDEN)):
            b = {"1min": _pct(minute[key], minute), "1h": _pct(hour[key], hour),
                 "session": _pct(self.session_counts[key], self.session_counts)}
            burden[key.lower()] = b
            labelled = hour["N"] + hour["S"] + hour["V"] + hour["F"]
            if labelled >= cfg.burden_min_beats and span >= cfg.burden_min_span_s \
                    and hour[key] >= cfg.burden_min_ectopics and b["1h"] >= cfg.burden_amber_pct:
                sev = "orange" if key == "V" and b["1h"] > cfg.burden_orange_pct else "amber"
                over = "hour" if span >= HISTORY_S else f"{span / 60:.0f} min"
                findings.append(Finding(rule, sev, end - span, end, b["1h"],
                                        f"{key} burden {b['1h']:.1f}% of labelled beats over the last {over} "
                                        f"(1 min {b['1min']:.1f}%, session {b['session']:.1f}%)"))

        # --- irregularity (60 s) and HRV (5 min) over NN intervals ---
        nn = [(t, rr, i) for t, rr, i in self._nn if t < end]
        irr = [x for x in nn if x[0] >= end - cfg.irregular_window_s]
        irregular = False
        if len(irr) >= cfg.irregular_min_intervals:
            rmssd, nd = _rmssd([x[1] for x in irr], [x[2] for x in irr])
            ratio = rmssd / float(np.mean([x[1] for x in irr]))
            if nd >= cfg.irregular_min_intervals // 2 and ratio > cfg.irregular_ratio:
                irregular = True
                self._irr_streak += 1
                sev = "orange" if self._irr_streak >= cfg.irregular_orange_windows else "amber"
                findings.append(Finding(
                    IRREGULAR, sev, max(irr[0][0] - irr[0][1], end - cfg.irregular_window_s), end, ratio,
                    f"RR irregularity (RMSSD / mean RR) {ratio:.2f} over {cfg.irregular_window_s:.0f} s, "
                    f"ectopic beats excluded; {self._irr_streak} consecutive window(s). Not a diagnosis"))
        if not irregular:
            self._irr_streak = 0
        hrv = [x for x in nn if x[0] >= end - cfg.hrv_window_s]
        sdnn = rmssd_ms = None
        if len(hrv) >= cfg.hrv_min_intervals:
            sdnn = round(float(np.std([x[1] for x in hrv], ddof=1)) * 1000.0, 1)
            r, nd = _rmssd([x[1] for x in hrv], [x[2] for x in hrv])
            rmssd_ms = round(r * 1000.0, 1) if nd else None

        findings.sort(key=lambda f: (-TIER_RANK[f.severity], f.start_s, f.rule_id))
        window = {
            "start_s": round(start, 3), "end_s": round(end, 3), "hr_bpm": round(hr, 1) if hr is not None else None,
            "findings": [f.to_dict() for f in findings],
            "s_burden_pct": round(burden["s"]["1min"], 2), "v_burden_pct": round(burden["v"]["1min"], 2),
            "sqi": round(float(sqi), 3), "sdnn_ms": sdnn, "rmssd_ms": rmssd_ms,
            "beat_counts": counts, "rule_version": self.rule_version,
        }
        return WindowResult(window, findings, burden)
