"""Alert engine (BRD §11) and risk engine: confirmation, dedup, cooldown, caps, gating, decay."""
import pytest

from cardiosense.core import ALERT_POLICY, CLINICAL_RULES, RULE_TIER, AlertEngine, Finding, RiskEngine, RulesConfig
from cardiosense.core.types import TIER_RANK


def F(rule, sev=None, value=1.0, start=0.0, end=10.0):
    return Finding(rule, sev or RULE_TIER[rule], start, end, value, f"{rule} detail")


def fire(eng, findings, k, suppressed=False):
    """Window k (10 s each) -> [(rule, tier, reason)]."""
    return [(d.finding.rule_id, d.tier, d.reason) for d in eng.on_window(findings, (k + 1) * 10.0, suppressed)]


# ---------------------------------------------------------------- tier table
def test_tier_table_matches_brd():
    assert RULE_TIER == {
        "BRADY": "amber", "TACHY": "amber", "V_COUPLET": "amber", "BIGEMINY": "amber", "TRIGEMINY": "amber",
        "S_RUN": "amber", "S_BURDEN": "amber", "V_BURDEN": "amber", "IRREGULAR": "amber",
        "PAUSE": "orange", "V_RUN": "orange",
        "BRADY_SEVERE": "red", "TACHY_SEVERE": "red", "PAUSE_CRITICAL": "red", "V_SUSTAINED": "red"}
    assert set(ALERT_POLICY) == set(CLINICAL_RULES)
    assert "LEAD_OFF" not in ALERT_POLICY and "LOW_SQI" not in ALERT_POLICY
    assert ALERT_POLICY["S_RUN"][1] == ALERT_POLICY["S_BURDEN"][1] == "amber"
    for rule, (title, _) in ALERT_POLICY.items():
        assert "fib" not in title.lower() and "diagnos" not in title.lower()
    assert ALERT_POLICY["IRREGULAR"][0] == "Irregular rhythm — review"


# -------------------------------------------------------------- confirmation
def test_amber_fires_in_the_first_window():
    assert fire(AlertEngine(), [F("V_COUPLET")], 0) == [("V_COUPLET", "amber", "new")]


@pytest.mark.parametrize("rule", ["V_RUN", "PAUSE", "BRADY_SEVERE", "TACHY_SEVERE"])
def test_orange_and_red_need_two_consecutive_windows(rule):
    eng = AlertEngine()
    assert fire(eng, [F(rule)], 0) == []
    assert fire(eng, [F(rule)], 1) == [(rule, RULE_TIER[rule], "new")]
    eng = AlertEngine()                       # not consecutive: never confirmed
    assert fire(eng, [F(rule)], 0) == [] and fire(eng, [], 1) == [] and fire(eng, [F(rule)], 2) == []


def test_amber_window_does_not_confirm_a_red_one():
    eng = AlertEngine()
    assert fire(eng, [F("BRADY")], 0) == [("BRADY", "amber", "new")]
    assert fire(eng, [F("BRADY_SEVERE")], 1) == []
    assert fire(eng, [F("BRADY_SEVERE")], 2) == [("BRADY_SEVERE", "red", "new")]


def test_critical_pause_fires_at_once_and_is_not_repeated_by_its_window():
    eng = AlertEngine()
    p = F("PAUSE_CRITICAL", value=3.4, start=12.0, end=15.4)
    d = eng.on_immediate(p, 15.4, suppressed=False)
    assert [(x.finding.rule_id, x.tier, x.reason) for x in d] == [("PAUSE_CRITICAL", "red", "new")]
    assert eng.on_immediate(p, 15.9, False) == []
    assert fire(eng, [p], 1) == []
    eng = AlertEngine()                       # also without the immediate path: no confirmation needed
    assert fire(eng, [p], 1) == [("PAUSE_CRITICAL", "red", "new")]
    assert AlertEngine().on_immediate(F("V_RUN"), 5.0, False) == []      # only pauses > 3 s are immediate


def test_sustained_v_inherits_the_confirmation_of_its_v_run():
    eng = AlertEngine()
    assert fire(eng, [F("V_RUN")], 0) == []
    assert fire(eng, [F("V_RUN")], 1) == [("V_RUN", "orange", "new")]
    assert fire(eng, [F("V_RUN")], 2) == []
    assert fire(eng, [F("V_SUSTAINED")], 3) == [("V_SUSTAINED", "red", "new")]
    eng = AlertEngine()                       # out of the blue it still needs two windows
    assert fire(eng, [F("V_SUSTAINED")], 0) == [] and fire(eng, [F("V_SUSTAINED")], 1) != []


def test_irregular_amber_then_orange_escalation():
    eng = AlertEngine()
    assert fire(eng, [F("IRREGULAR", "amber")], 0) == [("IRREGULAR", "amber", "new")]
    assert fire(eng, [F("IRREGULAR", "orange")], 1) == [("IRREGULAR", "orange", "escalated")]
    assert fire(eng, [F("IRREGULAR", "orange")], 2) == []


# ------------------------------------------------------- dedup and cooldown
def test_one_alert_per_rule_per_episode_then_renotify_after_cooldown():
    eng = AlertEngine()
    fired = [fire(eng, [F("V_RUN"), F("V_RUN", start=3, end=4)], k) for k in range(100)]
    hits = [k for k, f in enumerate(fired) if f]
    assert hits == [1, 91]                    # confirmed at window 2; orange cooldown 900 s
    assert fired[1] == [("V_RUN", "orange", "new")] and fired[91] == [("V_RUN", "orange", "renotify")]


def test_new_episode_after_the_finding_has_been_absent():
    eng = AlertEngine()
    assert fire(eng, [F("V_COUPLET")], 0) != []
    for k in range(1, 6):
        assert fire(eng, [], k) == []
    assert fire(eng, [F("V_COUPLET")], 6) == []           # 5 clean windows: same episode
    for k in range(7, 13):
        fire(eng, [], k)
    assert fire(eng, [F("V_COUPLET")], 13) == [("V_COUPLET", "amber", "new")]


def test_state_change_renotifies_only_upwards():
    eng = AlertEngine()
    assert fire(eng, [F("V_BURDEN", "amber")], 0) == [("V_BURDEN", "amber", "new")]
    assert fire(eng, [F("V_BURDEN", "orange")], 1) == []                # orange needs confirmation
    assert fire(eng, [F("V_BURDEN", "orange")], 2) == [("V_BURDEN", "orange", "escalated")]
    assert fire(eng, [F("V_BURDEN", "amber")], 3) == []                 # downgrade: silent
    assert fire(eng, [F("V_BURDEN", "orange")], 4) == [] and fire(eng, [F("V_BURDEN", "orange")], 5) == []


def test_cooldown_is_configurable():
    eng = AlertEngine(RulesConfig.from_dict({"alert_cooldown_s": {"amber": 30}, "alert_confirm_windows": 3}))
    assert [bool(fire(eng, [F("BRADY")], k)) for k in range(7)] == [True, False, False, True, False, False, True]
    assert [bool(fire(eng, [F("V_RUN")], k)) for k in range(7, 11)] == [False, False, True, False]


# ------------------------------------------------------------- safety rules
def test_s_only_findings_never_exceed_amber():
    eng = AlertEngine()
    out = []
    for k in range(4):                        # even if a (mis)configured rule engine marks them red
        out += fire(eng, [F("S_RUN", "red"), F("S_BURDEN", "orange")], k)
    assert out == [("S_BURDEN", "amber", "new"), ("S_RUN", "amber", "new")]


def test_unknown_rules_and_device_tasks_never_alert():
    eng = AlertEngine()
    assert fire(eng, [F("LEAD_OFF", "red"), F("LOW_SQI", "red"), F("F_BEAT", "red"), F("UNKNOWN_BEATS", "red")], 0) == []
    assert fire(eng, [F("LEAD_OFF", "red"), F("LOW_SQI", "red"), F("F_BEAT", "red")], 1) == []


def test_low_sqi_suppresses_alerts_and_breaks_confirmation():
    eng = AlertEngine()
    assert fire(eng, [F("V_COUPLET"), F("BRADY_SEVERE")], 0, suppressed=True) == []
    assert eng.on_immediate(F("PAUSE_CRITICAL"), 5.0, suppressed=True) == []
    assert fire(eng, [F("BRADY_SEVERE")], 1) == []                      # first *clean* window
    assert fire(eng, [F("BRADY_SEVERE")], 2, suppressed=True) == []     # streak broken
    assert fire(eng, [F("BRADY_SEVERE")], 3) == []
    assert fire(eng, [F("BRADY_SEVERE")], 4) == [("BRADY_SEVERE", "red", "new")]


def test_candidate_shape():
    eng = AlertEngine()
    d = eng.on_window([F("V_COUPLET")], 10.0, False)[0]
    c = d.candidate({"start_s": 0.0, "end_s": 10.0, "fs": 250, "strip_mv": [], "beats": [], "window": {},
                     "rule_version": "rules-1.0.0", "model_version": "m"})
    assert set(c) == {"rule_id", "tier", "title", "detail", "evidence"}
    assert c["title"] == "V couplet" and c["tier"] == "amber" and c["evidence"]["reason"] == "new"


# ----------------------------------------------------------------------- risk
def risk(findings, burden=None):
    return RiskEngine().instant(findings, burden)


def test_risk_is_reproducible_and_explained():
    fs = [F("V_RUN"), F("V_COUPLET"), F("V_COUPLET", start=5), F("TACHY")]
    a, b = risk(fs), risk(list(reversed(fs)))
    assert a == b
    score, tier, factors = a
    assert tier == "orange" and score == 60 + 9 + 9
    assert sum(f["points"] for f in factors) == score
    assert all(set(f) == {"text", "points", "rule_id"} for f in factors)
    assert factors[0]["rule_id"] == "V_RUN" and any("2 × V couplet" in f["text"] for f in factors)


def test_risk_tier_always_equals_the_most_severe_finding():
    assert risk([]) == (0, "green", [])
    for rule in CLINICAL_RULES:
        for sev in {RULE_TIER[rule], "orange" if rule in ("V_BURDEN", "IRREGULAR") else RULE_TIER[rule]}:
            score, tier, factors = risk([F(rule, sev)])
            assert tier == sev and 0 < score <= 100, rule
    every_amber = [F(r) for r in CLINICAL_RULES if RULE_TIER[r] == "amber"]
    score, tier, factors = risk(every_amber)
    assert tier == "amber" and score == 49 and factors[-1]["rule_id"] == "RISK_BAND"
    assert sum(f["points"] for f in factors) == score
    assert risk([F(r) for r in CLINICAL_RULES])[:2] == (100, "red")


def test_s_only_and_f_unknown_never_raise_risk_above_amber():
    score, tier, _ = risk([F("S_RUN"), F("S_BURDEN"), F("S_RUN", start=4)])
    assert tier == "amber" and score < 50
    assert risk([F("F_BEATS", "red"), F("LOW_SQI", "red")]) == (0, "green", [])


def test_trend_factor():
    burden = {"v": {"1min": 12.5, "1h": 4.0, "session": 4.0}, "s": {"1min": 0, "1h": 0, "session": 0}}
    score, tier, factors = risk([F("V_BURDEN", "amber", 4.0)], burden)
    assert score == 35 and "rising" in factors[-1]["text"] and factors[-1]["points"] == 5
    flat = {"v": {"1min": 4.0, "1h": 4.0, "session": 4.0}}
    assert risk([F("V_BURDEN", "amber", 4.0)], flat)[0] == 30


def test_no_downgrade_without_evidence():
    eng = RiskEngine()
    up = lambda fs, sqi=1.0, sup=False, k=[0]: (k.__setitem__(0, k[0] + 1), eng.update(fs, None, sqi, sup, k[0] * 10.0))[1]
    assert up([F("V_RUN")])["tier"] == "orange"
    r = up([])
    assert r["tier"] == "orange" and r["score"] == 50 and r["factors"][-1]["rule_id"] == "RISK_HOLD"
    assert "V_RUN detail" in r["factors"][-1]["text"] and sum(f["points"] for f in r["factors"]) == r["score"]
    assert up([], sqi=0.2, sup=True)["tier"] == "orange"          # noisy windows are not evidence
    assert up([], sqi=0.2, sup=True)["tier"] == "orange"
    assert up([])["tier"] == "orange"
    assert up([])["tier"] == "amber"                              # third clean window: one step down
    assert [up([])["tier"] for _ in range(3)] == ["amber", "amber", "green"]
    assert up([])["score"] == 0
    # a finding during the decay resets the count; escalation is immediate
    assert up([F("PAUSE_CRITICAL")])["tier"] == "red"
    assert [up([F("V_COUPLET")])["tier"] for _ in range(3)] == ["red", "red", "orange"]


def test_suppressed_window_freezes_risk_and_says_why():
    eng = RiskEngine()
    eng.update([F("V_COUPLET")], None, 0.9, False, 10.0)
    r = eng.update([F("V_SUSTAINED")], None, 0.2, True, 20.0)     # garbage must not escalate either
    assert r["tier"] == "amber" and r["score"] == 30
    assert r["factors"] == [{"text": r["factors"][0]["text"], "points": 0, "rule_id": "LOW_SQI"}]
    assert "suppressed" in r["factors"][0]["text"]
    assert TIER_RANK[r["tier"]] == 1
