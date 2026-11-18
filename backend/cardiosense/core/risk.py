"""Reproducible risk score (0-100) and tier from a window's findings, burden trend and SQI.

score = points of the strongest rule + `risk_secondary_weight` x points of every
other rule (+ trend points), clamped to the band of the highest finding severity,
so the tier always equals the most severe finding (S-only stays amber; F and
Unknown beats have no rule and add nothing). Every contribution is a factor
with its points and rule id; the factors' points sum to the score.

"No downgrade without evidence": the reported tier only steps down, one level
at a time, after `risk_decay_clean_windows` consecutive good-quality windows
that score lower. Low-quality windows freeze the tier in both directions.
"""
from __future__ import annotations

from .config import RulesConfig
from .types import LOW_SQI, RULE_LABELS, TIER_RANK, TIERS, Finding


class RiskEngine:
    def __init__(self, cfg: RulesConfig | None = None):
        self.cfg = cfg or RulesConfig()
        self.tier = "green"
        self.score = 0
        self._clean = 0
        self._held_by = None        # (label, time_s) of what set the current tier

    def _tier_of(self, score: float) -> str:
        fl = self.cfg.risk_tier_floor
        return max((t for t in TIERS if score >= fl[t]), key=TIER_RANK.__getitem__)

    def _band(self, tier: str) -> tuple[int, int]:
        fl = self.cfg.risk_tier_floor
        i = TIER_RANK[tier]
        return int(fl[tier]), (int(fl[TIERS[i + 1]]) - 1 if i + 1 < len(TIERS) else 100)

    def instant(self, findings: list[Finding], burden: dict | None = None) -> tuple[int, str, list[dict]]:
        """Stateless score for one window: (score, tier, factors)."""
        cfg = self.cfg
        per_rule: dict[str, list[Finding]] = {}
        for f in findings:
            if f.rule_id in cfg.risk_points:
                per_rule.setdefault(f.rule_id, []).append(f)
        items = []
        for rule, fs in per_rule.items():
            sev = max((f.severity for f in fs), key=TIER_RANK.__getitem__)
            lo, hi = self._band(sev)
            pts = min(max(int(cfg.risk_points[rule]), lo + 5 if cfg.risk_points[rule] < lo else lo), hi)
            top = max(fs, key=lambda f: (TIER_RANK[f.severity], f.value))
            text = top.detail if len(fs) == 1 else f"{len(fs)} × {RULE_LABELS.get(rule, rule)} — {top.detail}"
            items.append((pts, TIER_RANK[sev], rule, text))
        items.sort(key=lambda x: (-x[0], -x[1], x[2]))

        factors, raw = [], 0
        for n, (pts, _, rule, text) in enumerate(items):
            p = pts if n == 0 else int(round(cfg.risk_secondary_weight * pts))
            raw += p
            factors.append({"text": text, "points": p, "rule_id": rule})
        v = (burden or {}).get("v")
        if items and v and v["1min"] >= v["1h"] + 5.0 and v["1min"] >= 1.5 * v["1h"]:
            raw += cfg.risk_trend_points
            factors.append({"text": f"V ectopy rising: {v['1min']:.1f}% in the last minute vs {v['1h']:.1f}% "
                                    f"over the last hour", "points": cfg.risk_trend_points, "rule_id": "V_BURDEN"})
        top_sev = TIERS[max((i[1] for i in items), default=0)]
        lo, hi = self._band(top_sev)
        score = min(max(raw, lo), hi) if items else 0
        if score != raw:
            factors.append({"text": f"Score limited to the {top_sev.capitalize()} band: no finding above that level",
                            "points": score - raw, "rule_id": "RISK_BAND"})
        return score, self._tier_of(score), factors

    def update(self, findings: list[Finding], burden: dict | None, sqi: float, suppressed: bool,
               end_s: float) -> dict:
        """Stateful risk for the next window (in order). Returns the §2.2 `risk` object."""
        cfg = self.cfg
        if suppressed:
            return {"score": self.score, "tier": self.tier, "factors": [{
                "text": f"Signal quality low (SQI {sqi:.2f}): risk held at {self.tier.capitalize()}, "
                        f"clinical alerts suppressed", "points": 0, "rule_id": LOW_SQI}]}
        score, tier, factors = self.instant(findings, burden)
        if TIER_RANK[tier] >= TIER_RANK[self.tier]:
            if tier != "green":
                self._held_by = (factors[0]["text"], end_s)
            self.tier, self._clean = tier, 0
        else:
            self._clean += 1
            if self._clean >= cfg.risk_decay_clean_windows:
                self.tier = TIERS[max(TIER_RANK[self.tier] - 1, TIER_RANK[tier])]
                self._clean = 0
            floor = self._band(self.tier)[0]
            if score < floor:
                what, when = self._held_by or ("earlier finding", end_s)
                left = cfg.risk_decay_clean_windows - self._clean
                factors.append({
                    "text": f"Held at {self.tier.capitalize()} after: {what} (window ending {when:.0f} s); "
                            f"steps down after {left} more clean window(s)",
                    "points": floor - score, "rule_id": "RISK_HOLD"})
                score = floor
        if sqi < 0.7:
            factors.append({"text": f"Signal quality reduced (SQI {sqi:.2f})", "points": 0, "rule_id": LOW_SQI})
        self.score = int(score)
        return {"score": self.score, "tier": self.tier, "factors": factors}
