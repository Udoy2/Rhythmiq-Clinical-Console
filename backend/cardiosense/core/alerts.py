"""Alert engine (BRD §11): tier mapping, confirmation, deduplication, cooldown, SQI gating.

* Tier = finding severity, capped by `ALERT_POLICY` (S-only rules never exceed amber).
  There is no rule for F or Unknown beats, so they can never alert alone.
* Orange/red need the finding at that level in `alert_confirm_windows` consecutive
  windows. Exceptions: a pause > 3 s fires at once; IRREGULAR is orange only *because* it
  persisted; a sustained V pattern inherits the windows of the V run it grew out of.
* One alert per rule per episode. An episode ends after `alert_episode_clear_windows`
  windows without the finding. Within an episode the rule re-notifies only when its tier
  rises ("escalated") or the tier's cooldown has elapsed ("renotify").
* A suppressed window (low SQI / lead-off) raises nothing and breaks confirmation streaks.

Alerts are never closed here: the engine only proposes candidates.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import RulesConfig
from .types import (BIGEMINY, BRADY, BRADY_SEVERE, IRREGULAR, PAUSE, PAUSE_CRITICAL, S_BURDEN, S_RUN, TACHY,
                    TACHY_SEVERE, TIER_RANK, TRIGEMINY, V_BURDEN, V_COUPLET, V_RUN, V_SUSTAINED, Finding, min_tier)

# rule -> (alert title, highest tier the rule may ever reach)
ALERT_POLICY = {
    BRADY: ("Low heart rate", "amber"), BRADY_SEVERE: ("Very low heart rate", "red"),
    TACHY: ("High heart rate", "amber"), TACHY_SEVERE: ("Very high heart rate", "red"),
    PAUSE: ("Pause", "orange"), PAUSE_CRITICAL: ("Pause longer than 3 s", "red"),
    V_COUPLET: ("V couplet", "amber"), V_RUN: ("Non-sustained V run", "orange"),
    V_SUSTAINED: ("Sustained V pattern", "red"),
    BIGEMINY: ("Bigeminy pattern", "amber"), TRIGEMINY: ("Trigeminy pattern", "amber"),
    S_RUN: ("S run", "amber"), S_BURDEN: ("S ectopic burden", "amber"),
    V_BURDEN: ("V ectopic burden", "orange"), IRREGULAR: ("Irregular rhythm — review", "orange"),
}
IMMEDIATE = frozenset({PAUSE_CRITICAL})      # fire without confirmation
SELF_CONFIRMED = frozenset({IRREGULAR})      # severity already encodes persistence over windows
CONFIRM_CREDIT = {V_SUSTAINED: V_RUN}        # streak inherited from the rule it escalates from
ORANGE = TIER_RANK["orange"]


@dataclass
class AlertDecision:
    finding: Finding
    tier: str
    reason: str             # new | escalated | renotify

    def candidate(self, evidence: dict) -> dict:
        return {"rule_id": self.finding.rule_id, "tier": self.tier, "title": ALERT_POLICY[self.finding.rule_id][0],
                "detail": self.finding.detail, "evidence": {**evidence, "reason": self.reason}}


class AlertEngine:
    def __init__(self, cfg: RulesConfig | None = None):
        self.cfg = cfg or RulesConfig()
        self._active: dict[str, list] = {}    # rule -> [tier, last_notify_s, windows_absent]
        self._streak: dict[str, int] = {}     # rule -> consecutive windows at orange or above

    def _notify(self, f: Finding, tier: str, now_s: float) -> AlertDecision | None:
        st = self._active.get(f.rule_id)
        if st is None:
            reason = "new"
        elif TIER_RANK[tier] > TIER_RANK[st[0]]:
            reason = "escalated"
        elif now_s - st[1] >= self.cfg.alert_cooldown_s[tier]:
            reason = "renotify"
        else:
            st[2] = 0
            return None
        self._active[f.rule_id] = [tier if st is None else max(tier, st[0], key=TIER_RANK.__getitem__), now_s, 0]
        return AlertDecision(f, tier, reason)

    def on_immediate(self, finding: Finding, now_s: float, suppressed: bool) -> list[AlertDecision]:
        """Findings that must not wait for the window to close (pause > 3 s)."""
        if suppressed or finding.rule_id not in IMMEDIATE:
            return []
        tier = min_tier(finding.severity, ALERT_POLICY[finding.rule_id][1])
        d = self._notify(finding, tier, now_s)
        return [d] if d else []

    def on_window(self, findings: list[Finding], end_s: float, suppressed: bool) -> list[AlertDecision]:
        if suppressed:
            self._streak = {}
            return []
        best: dict[str, tuple[Finding, str]] = {}
        for f in findings:
            if f.rule_id not in ALERT_POLICY:
                continue
            tier = min_tier(f.severity, ALERT_POLICY[f.rule_id][1])
            cur = best.get(f.rule_id)
            if cur is None or (TIER_RANK[tier], f.value) > (TIER_RANK[cur[1]], cur[0].value):
                best[f.rule_id] = (f, tier)

        prev = self._streak
        self._streak = {}
        for rule, (_, tier) in best.items():
            if TIER_RANK[tier] >= ORANGE:
                self._streak[rule] = (prev.get(rule) or prev.get(CONFIRM_CREDIT.get(rule), 0)) + 1

        out = []
        for rule, (f, tier) in sorted(best.items(), key=lambda kv: (-TIER_RANK[kv[1][1]], kv[0])):
            if tier == "green":
                continue
            if TIER_RANK[tier] >= ORANGE and rule not in IMMEDIATE and rule not in SELF_CONFIRMED \
                    and self._streak[rule] < self.cfg.alert_confirm_windows:
                if rule in self._active:
                    self._active[rule][2] = 0
                continue
            d = self._notify(f, tier, end_s)
            if d:
                out.append(d)
        for rule in [r for r in self._active if r not in best]:
            self._active[rule][2] += 1
            if self._active[rule][2] >= self.cfg.alert_episode_clear_windows:
                del self._active[rule]
        return out
