"""Rhythm rules engine: every rule on synthetic beat sequences, including boundaries (BRD §8.4)."""
import numpy as np
import pytest

from cardiosense.core import RULE_VERSION, RhythmEngine, RulesConfig


def feed(eng, labels, rr=0.8, start=0.5, seg=0):
    """Add beats `labels` (string or list) spaced `rr` s (scalar or per-beat list). Returns beat times."""
    labels = list(labels)
    rrs = [rr] * len(labels) if np.isscalar(rr) else list(rr)
    t, times = start, []
    for lab, r in zip(labels, rrs):
        eng.add_beat(t, {"U": "Unknown"}.get(lab, lab), seg)
        times.append(t)
        t += r
    return times


def windows(eng, n, seg=None, w=10.0):
    return [eng.close_window(k * w, (k + 1) * w, 1.0, seg) for k in range(n)]


def timeline(labels, rr, n, start=0.5, w=10.0):
    """Like the live pipeline: a window is closed before the first beat at or after its end is added."""
    eng, out, t = RhythmEngine(), [], start
    rrs = [rr] * len(labels) if np.isscalar(rr) else list(rr)
    for lab, r in zip(labels, rrs):
        while t >= (len(out) + 1) * w:
            out.append(eng.close_window(len(out) * w, (len(out) + 1) * w))
        eng.add_beat(t, lab)
        t += r
    while len(out) < n:
        out.append(eng.close_window(len(out) * w, (len(out) + 1) * w))
    return out


def ids(res):
    return [f.rule_id for f in res.findings]


def run(labels, rr=0.8, n=1, **kw):
    eng = RhythmEngine(RulesConfig.from_dict(kw or None))
    feed(eng, labels, rr)
    return windows(eng, n)


# ------------------------------------------------------------------ heart rate
@pytest.mark.parametrize("rr,expected", [
    (0.8, []), (1.19, []), (1.21, ["BRADY"]), (1.49, ["BRADY"]), (1.52, ["BRADY_SEVERE"]),
    (0.61, []), (0.59, ["TACHY"]), (0.41, ["TACHY"]), (0.39, ["TACHY_SEVERE"])])
def test_rate_thresholds(rr, expected):
    res = run("N" * int(9.4 / rr), rr)[0]
    assert ids(res) == expected
    assert res.window["hr_bpm"] == pytest.approx(60 / rr, abs=0.06)
    if expected:
        assert res.findings[0].severity == ("red" if "SEVERE" in expected[0] else "amber")


def test_hr_is_median_rr_of_n_and_s_beats_only():
    # N beats at 1.0 s with interpolated V beats: V-N and N-V intervals are 0.5 s and must not count
    eng = RhythmEngine()
    t = 0.5
    for i in range(9):
        eng.add_beat(t, "N"); t += 1.0
    res = eng.close_window(0, 10)
    assert res.window["hr_bpm"] == pytest.approx(60.0)
    eng = RhythmEngine()
    feed(eng, "NSNSNSNSNSNS", 0.75)
    assert eng.close_window(0, 10).window["hr_bpm"] == pytest.approx(80.0)


def test_hr_none_without_enough_intervals_and_unknown_excluded():
    res = run("NN", 1.0)[0]
    assert res.window["hr_bpm"] is None and ids(res) == []
    res = run("U" * 20, 0.3)[0]            # would be 200 bpm, but Unknown beats carry no rate
    assert res.window["hr_bpm"] is None and ids(res) == []
    assert res.window["beat_counts"]["Unknown"] == 20


def test_rules_only_mode_uses_all_beats_for_rate():
    eng = RhythmEngine(rules_only=True)
    feed(eng, "U" * 6, 1.6)
    res = eng.close_window(0, 10)
    assert ids(res) == ["BRADY_SEVERE"] and res.window["hr_bpm"] == pytest.approx(37.5)


# ----------------------------------------------------------------------- pause
@pytest.mark.parametrize("gap,expected", [(2.0, None), (2.01, "PAUSE"), (3.0, "PAUSE"), (3.01, "PAUSE_CRITICAL")])
def test_pause_boundaries(gap, expected):
    eng = RhythmEngine()
    for t in (1.0, 2.0, 3.0):
        eng.add_beat(t, "N")
    crit = eng.add_beat(3.0 + gap, "N")
    eng.add_beat(3.8 + gap, "N")
    res = eng.close_window(0, 10)
    found = [f for f in res.findings if f.rule_id.startswith("PAUSE")]
    assert [f.rule_id for f in found] == ([expected] if expected else [])
    assert bool(crit) == (expected == "PAUSE_CRITICAL")
    if expected:
        assert found[0].start_s == 3.0 and found[0].value == pytest.approx(gap)
        assert found[0].severity == ("red" if expected == "PAUSE_CRITICAL" else "orange")


def test_pause_between_unknown_beats_still_counts():
    eng = RhythmEngine()
    feed(eng, "UU", 2.5, start=1.0)
    assert "PAUSE" in ids(eng.close_window(0, 10))


def test_ongoing_pause_reported_at_window_end_and_once_when_it_completes():
    eng = RhythmEngine()
    feed(eng, "NNNNN", 1.0, start=1.0)                 # last beat at 5.0
    w1 = eng.close_window(0, 10, seg=0)                # 5 s without a beat at the window end
    p = [f for f in w1.findings if f.rule_id == "PAUSE_CRITICAL"]
    assert len(p) == 1 and p[0].start_s == 5.0 and p[0].end_s == 10.0 and "ongoing" in p[0].detail
    assert eng.add_beat(11.0, "N")[0].value == pytest.approx(6.0)
    feed(eng, "N" * 20, 1.0, start=12.0)
    w2 = eng.close_window(10, 20, seg=0)
    p2 = [f for f in w2.findings if f.rule_id == "PAUSE_CRITICAL"]
    assert len(p2) == 1 and p2[0].start_s == 5.0 and p2[0].end_s == 11.0      # same episode, completed
    assert not [f for f in eng.close_window(20, 30, seg=0).findings if f.rule_id.startswith("PAUSE")]


def test_no_ongoing_pause_without_segment_or_before_first_beat():
    eng = RhythmEngine()
    assert eng.ongoing_pause(50.0, 0) is None
    eng.add_beat(1.0, "N", seg=0)
    assert eng.ongoing_pause(9.0, 1) is None           # signal is in another segment now
    assert eng.close_window(0, 10).findings == []      # seg not given: only completed pauses


def test_pause_rejected_when_signal_check_sees_missed_beats():
    calls = []
    eng = RhythmEngine(pause_check=lambda a, b: calls.append((a, b)) or b - a < 2)
    feed(eng, "NNN", 1.0, start=1.0)
    assert eng.add_beat(7.0, "N") == []
    assert eng.ongoing_pause(12.0, 0) is None
    assert eng.window_stats(0, 10)["rr_bad"] == 1
    assert not [f for f in eng.close_window(0, 10, seg=0).findings if f.rule_id.startswith("PAUSE")]
    assert (3.0, 7.0) in calls and (7.0, 12.0) in calls


def test_intervals_hiding_missed_beats_do_not_count_for_rate_or_irregularity():
    # every second beat undetected: apparent 37.5 bpm. With the signal check refuting those intervals
    # there is no rate at all rather than a false "severe bradycardia"
    eng = RhythmEngine()
    feed(eng, "N" * 6, 1.6)
    assert ids(eng.close_window(0, 10)) == ["BRADY_SEVERE"]
    eng = RhythmEngine(pause_check=lambda a, b: False)
    feed(eng, "N" * 6, 1.6)
    res = eng.close_window(0, 10)
    assert res.window["hr_bpm"] is None and res.findings == [] and eng.window_stats(0, 10)["rr_bad"] == 5
    # short intervals are never screened (no room for a hidden beat)
    eng = RhythmEngine(pause_check=lambda a, b: False)
    feed(eng, "N" * 15, 0.6)
    assert eng.close_window(0, 10).window["hr_bpm"] == pytest.approx(100.0)
    # mixed: half the intervals are misses -> rate from the verified ones, and no irregularity from the rest
    eng = RhythmEngine(pause_check=lambda a, b: b - a < 1.2)
    feed(eng, "N" * 110, [0.8, 0.8, 1.6] * 37)
    ws = windows(eng, 11)
    assert all(w.window["hr_bpm"] == pytest.approx(75.0) for w in ws)
    assert all("IRREGULAR" not in ids(w) and "BRADY" not in ids(w) for w in ws)


# ------------------------------------------------------------------ V patterns
def test_v_couplet_and_run_and_isolated_v():
    assert ids(run("NNVNNN")[0]) == []
    res = run("NNVVNNNNNN")[0]
    assert ids(res) == ["V_COUPLET"] and res.findings[0].severity == "amber" and res.findings[0].value == 2
    res = run("NNVVVNNNNN")[0]
    assert ids(res) == ["V_RUN"] and res.findings[0].severity == "orange" and res.findings[0].value == 3
    res = run("NVVNNVVVVN")[0]
    assert sorted(ids(res)) == ["V_COUPLET", "V_RUN"]


def test_unknown_or_fusion_beat_breaks_a_v_run():
    assert ids(run("NVVUVNNNN")[0]) == ["V_COUPLET"]
    assert ids(run("NVVFVNNNN")[0]) == ["V_COUPLET"]
    assert ids(run("NVUVUVNNN")[0]) == []


def test_v_run_spanning_window_boundary_is_one_episode():
    eng = RhythmEngine()
    feed(eng, "N" * 10 + "VV", 0.8, start=0.6)         # V at 8.6 and 9.4
    w1 = eng.close_window(0, 10)
    assert ids(w1) == ["V_COUPLET"]
    feed(eng, "VNNNNNNNNNNN", 0.8, start=10.2)          # third V in the next window
    w2, w3 = eng.close_window(10, 20), eng.close_window(20, 30)
    runs = [f for f in w2.findings if f.rule_id in ("V_RUN", "V_COUPLET")]
    assert len(runs) == 1 and runs[0].rule_id == "V_RUN" and runs[0].start_s == pytest.approx(8.6)
    assert runs[0].value == 3
    assert not [f for f in w3.findings if f.rule_id in ("V_RUN", "V_COUPLET")]


def test_sustained_v_needs_30_s_above_100_bpm():
    ws = timeline("NN" + "V" * 70, [0.8, 0.8] + [0.5] * 70, 4)         # V from 2.1 s at 120 bpm
    assert [("V_SUSTAINED" in ids(w), "V_RUN" in ids(w)) for w in ws] == [(False, True)] * 3 + [(True, False)]
    sus = [f for f in ws[3].findings if f.rule_id == "V_SUSTAINED"][0]
    assert sus.severity == "red" and sus.start_s == pytest.approx(2.1) and sus.end_s - sus.start_s >= 30
    assert len({f.start_s for w in ws for f in w.findings if f.rule_id in ("V_RUN", "V_SUSTAINED")}) == 1
    # 29.5 s of V beats: still non-sustained
    assert all("V_SUSTAINED" not in ids(w) for w in timeline("V" * 60, 0.5, 4, start=0.2))
    # 35 s of V beats at 86 bpm: long, but not above 100 bpm
    ws = timeline("V" * 51, 0.7, 4, start=0.2)
    assert all("V_SUSTAINED" not in ids(w) for w in ws) and "V_RUN" in ids(ws[3])


@pytest.mark.parametrize("pattern,rule", [("NV", "BIGEMINY"), ("NNV", "TRIGEMINY")])
def test_geminy_needs_six_cycles(pattern, rule):
    other = "TRIGEMINY" if rule == "BIGEMINY" else "BIGEMINY"
    res = run("NNN" + pattern * 5 + "NNNN", 0.5, n=2)
    assert rule not in ids(res[0]) + ids(res[1])
    labels = "NNN" + pattern * 6 + "NNNN"
    res = timeline(labels, 0.45, 2)
    found = [f for w in res for f in w.findings if f.rule_id == rule]
    assert found and found[-1].value == 6 and found[0].severity == "amber"
    assert found[0].start_s == pytest.approx(0.5 + 3 * 0.45)          # first normal beat of the first cycle
    assert len({f.start_s for f in found}) == 1                      # one episode, also across the boundary
    every = ids(res[0]) + ids(res[1])
    assert other not in every and "V_COUPLET" not in every
    # a 7th cycle extends the same episode
    res = timeline("NNN" + pattern * 7 + "NNNN", 0.45, 2)
    assert max(f.value for w in res for f in w.findings if f.rule_id == rule) == 7


def test_geminy_broken_by_unknown_beat_or_gap():
    assert "BIGEMINY" not in ids(run("NVNVNVUNVNVNVN", 0.5)[0])
    eng = RhythmEngine()
    feed(eng, "NVNVNV", 0.5, seg=0)
    feed(eng, "NVNVNV", 0.5, start=3.5, seg=1)
    assert "BIGEMINY" not in ids(eng.close_window(0, 10))


# ------------------------------------------------------------------ S patterns
def test_s_run_needs_three_beats_above_100_bpm_and_is_amber():
    res = run("NNSSSNN", [0.8, 0.8, 0.45, 0.45, 0.45, 0.8, 0.8])[0]
    assert ids(res) == ["S_RUN"] and res.findings[0].severity == "amber"
    assert ids(run("NNSSNNN", [0.8, 0.8, 0.45, 0.45, 0.8, 0.8, 0.8])[0]) == []
    assert ids(run("NNSSSNN", 0.7)[0]) == []                          # 86 bpm


# ---------------------------------------------------------------------- burden
def burden_engine(pattern, n, rr=0.6):
    eng = RhythmEngine()
    feed(eng, (pattern * n)[:n], rr, start=0.3)
    k = int(np.ceil((0.3 + n * rr) / 10))
    return windows(eng, k)[-1]


def test_v_burden_tiers_and_window_fields():
    res = burden_engine("N" * 19 + "V", 200)                          # 5 %
    f = [f for f in res.findings if f.rule_id == "V_BURDEN"]
    assert f and f[0].severity == "amber" and f[0].value == pytest.approx(5.0, abs=0.3)
    assert res.window["v_burden_pct"] == pytest.approx(5.0, abs=1.5) and res.window["s_burden_pct"] == 0
    assert res.burden["v"]["session"] == pytest.approx(5.0)
    res = burden_engine("NNNV", 200)                                  # 25 %
    assert [f.severity for f in res.findings if f.rule_id == "V_BURDEN"] == ["orange"]
    res = burden_engine("N" * 199 + "V", 400)                         # 0.5 %
    assert "V_BURDEN" not in ids(res)


def test_s_burden_never_above_amber_and_unknown_excluded():
    res = burden_engine("NS", 200)                                    # 50 % S
    f = [f for f in res.findings if f.rule_id == "S_BURDEN"]
    assert f and f[0].severity == "amber"
    res = burden_engine("NVUU", 200)                                  # V is half of the *labelled* beats
    assert res.burden["v"]["session"] == pytest.approx(50.0)


def test_burden_needs_minimum_evidence():
    eng = RhythmEngine()
    feed(eng, "NVNVNVNVNVNV", 0.8)
    assert "V_BURDEN" not in ids(eng.close_window(0, 10))             # 12 beats, 10 s
    eng = RhythmEngine()
    feed(eng, "NNNV" * 20, 0.5)
    assert "V_BURDEN" not in ids(windows(eng, 4)[-1])                 # 80 beats but only 40 s
    assert "V_BURDEN" in ids(burden_engine("NNNV", 140, 0.5))         # 70 s
    assert "V_BURDEN" not in ids(burden_engine("N" * 40 + "VV" + "N" * 200, 150, 0.6))   # 2 V beats = 1.3 %
    assert "V_BURDEN" in ids(burden_engine("N" * 40 + "VNVNV" + "N" * 200, 150, 0.6))    # 3 V beats = 2 %


def test_one_hour_burden_forgets_old_beats():
    eng = RhythmEngine()
    feed(eng, "NV" * 100, 0.5)                                        # heavy ectopy in the first 100 s
    eng.close_window(90, 100)
    feed(eng, "N" * 4700, 0.8, start=3700.0)                          # > 1 h later: clean
    res = eng.close_window(7450, 7460)
    assert res.burden["v"]["1h"] == 0 and res.burden["v"]["session"] > 1 and "V_BURDEN" not in ids(res)


# ------------------------------------------------------------ irregular / HRV
def irregular_rr(n, seed=1):
    return list(np.random.default_rng(seed).uniform(0.5, 1.1, n))


def test_irregular_rhythm_label_and_escalation_after_two_windows():
    eng = RhythmEngine()
    feed(eng, "N" * 120, irregular_rr(120))
    ws = windows(eng, 9)
    sev = [next((f.severity for f in w.findings if f.rule_id == "IRREGULAR"), None) for w in ws]
    first = next(i for i, s in enumerate(sev) if s)
    assert first >= 2                                                 # needs >= 30 NN intervals first
    assert sev[first] == "amber" and all(s == "orange" for s in sev[first + 1:])
    f = [f for f in ws[first].findings if f.rule_id == "IRREGULAR"][0]
    assert f.to_dict()["label"] == "Irregular rhythm — review"
    assert "AF" not in f.detail and "fibrillation" not in f.detail.lower() and f.value > 0.15


def test_regular_rhythm_with_ectopics_is_not_irregular():
    eng = RhythmEngine()
    # PVC with compensatory pause every 5th beat: the V-adjacent intervals are excluded
    feed(eng, "NNNNV" * 30, [0.8, 0.8, 0.8, 0.5, 1.1] * 30)
    assert all("IRREGULAR" not in ids(w) for w in windows(eng, 12))
    eng = RhythmEngine()
    feed(eng, "N" * 150, list(0.8 + 0.03 * np.sin(np.arange(150) / 3)))  # mild sinus arrhythmia
    assert all("IRREGULAR" not in ids(w) for w in windows(eng, 12))


def test_hrv_sdnn_rmssd_over_nn_intervals():
    eng = RhythmEngine()
    feed(eng, "N" * 61, [0.8, 0.9] * 31)
    res = windows(eng, 6)[-1]
    assert res.window["rmssd_ms"] == pytest.approx(100.0, abs=0.5)
    assert res.window["sdnn_ms"] == pytest.approx(50.4, abs=0.5)
    assert run("NNNNN")[0].window["sdnn_ms"] is None                  # too few intervals


# --------------------------------------------------------------------- gaps
def test_nothing_is_measured_across_a_gap():
    eng = RhythmEngine()
    feed(eng, "NNVV", 0.8, start=1.0, seg=0)                          # couplet ends the segment at 3.4
    feed(eng, "VNNN", 0.8, start=7.5, seg=1)                          # 4.1 s later, new segment starts with V
    res = eng.close_window(0, 10, seg=1)
    assert ids(res) == ["V_COUPLET"]                                  # no V run, no pause
    assert eng.window_stats(0, 10)["rr"] == pytest.approx([0.8] * 6)


# ------------------------------------------------------------------- config
def test_window_shape_and_version():
    res = run("NNNNNNNNNNN")[0]
    assert set(res.window) == {"start_s", "end_s", "hr_bpm", "findings", "s_burden_pct", "v_burden_pct", "sqi",
                               "sdnn_ms", "rmssd_ms", "beat_counts", "rule_version"}
    assert res.window["rule_version"] == RULE_VERSION == "rules-1.0.0"
    assert set(res.window["beat_counts"]) == {"N", "S", "V", "F", "Unknown"}


def test_org_overrides_change_thresholds_and_version():
    assert ids(run("N" * 9, 1.1)[0]) == []
    res = run("N" * 9, 1.1, brady_bpm=60)[0]
    assert ids(res) == ["BRADY"]
    assert res.window["rule_version"].startswith("rules-1.0.0+")
    assert ids(run("NNVVNNNNNN", v_run_min_beats=2)[0]) == ["V_RUN"]
    cfg = RulesConfig.from_dict({"alert_cooldown_s": {"red": 60}, "not_a_field": 1})
    assert cfg.alert_cooldown_s == {"amber": 3600.0, "orange": 900.0, "red": 60}
    assert RulesConfig.from_dict(None).version() == RULE_VERSION == RulesConfig.from_dict({}).version()


def test_findings_only_come_from_known_rules_and_f_unknown_have_none():
    res = run("FUFUFUFUFUFU", 0.8)[0]
    assert res.findings == []
