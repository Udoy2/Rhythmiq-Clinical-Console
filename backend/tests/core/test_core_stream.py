"""StreamProcessor on synthetic ECG: emission order, units, gaps, quality gating, alerts, model parity."""
import json

import numpy as np
import pytest

from cardiosense.core import RULE_VERSION, StreamProcessor, load_classifier
from cardiosense.core.bench import offline_agreement, r_peak_scores
from core_helpers import collect, stream, synth_ecg


def regular(fs, dur=40.0, rr=0.8, **kw):
    times = np.arange(0.5, dur - 0.3, rr)
    return times, synth_ecg(times, fs, dur, **kw)


def detected_ok(beats, times, fs, tol_s=0.03):
    got = np.array([b["time_s"] for b in beats])
    return len(got) == len(times) and np.abs(got - np.asarray(times)).max() <= tol_s


# ------------------------------------------------------------ basic contract
def test_load_classifier_returns_none_without_model_files(tmp_path):
    assert load_classifier(tmp_path) is None
    (tmp_path / "model_config.json").write_text("{}")
    assert load_classifier(tmp_path) is None


@pytest.mark.parametrize("fs", [60, 125, 250, 257, 360])
def test_rules_only_mode_detects_beats_at_any_device_rate(fs):
    times, x = regular(fs)
    sp = StreamProcessor(fs=fs, classifier=None)
    outs = stream(sp, x)
    beats = collect(outs, "beats")
    assert detected_ok(beats, times, fs)
    assert all(b["label"] == "Unknown" and b["confidence"] == 0 and b["model_version"] is None for b in beats)
    assert all(b["time_s"] == pytest.approx(b["sample"] / fs, abs=1e-3) for b in beats)
    assert sp.model_version is None and sp.rule_version == RULE_VERSION and not sp.model_loaded
    windows = collect(outs, "windows")
    assert [w["start_s"] for w in windows] == [0.0, 10.0, 20.0, 30.0]
    assert all(w["hr_bpm"] == pytest.approx(75, abs=1) and w["sqi"] > 0.9 for w in windows[:3])
    assert all(w["risk"]["tier"] == "green" for w in windows) and collect(outs, "alerts") == []


def test_output_shapes_are_json_and_match_the_contract(fake_clf):
    fs = 360
    times, x = regular(fs, 25.0)
    sp = StreamProcessor(fs=fs, classifier=fake_clf, adc_zero=None, adc_gain=None, rules_config=None)
    outs = stream(sp, x)
    json.dumps([o.to_dict() for o in outs])
    sig = [o.signal for o in outs if o.signal]
    assert all(set(s) == {"t0", "fs", "mv", "lead_off"} for s in sig)
    assert [s["t0"] for s in sig] == list(range(0, len(x), 36)) and sum(len(s["mv"]) for s in sig) == len(x)
    b = collect(outs, "beats")[3]
    assert set(b) == {"sample", "time_s", "label", "confidence", "probs", "model_version", "truth"}
    assert set(b["probs"]) == {"N", "S", "V", "F"} and b["model_version"] == "fake-test-model" == sp.model_version
    w = collect(outs, "windows")[0]
    assert set(w) == {"start_s", "end_s", "hr_bpm", "findings", "s_burden_pct", "v_burden_pct", "sqi", "sdnn_ms",
                      "rmssd_ms", "beat_counts", "rule_version", "risk"}
    assert set(w["risk"]) == {"score", "tier", "factors"}
    st = sp.stats()
    assert st["beats"] == len(collect(outs, "beats")) == sum(st["labels"].values())
    assert {"median", "p95", "max"} <= set(st["latency_s"]) and {"p50", "p95", "max", "mean"} <= set(st["push_ms"])


def test_display_signal_is_causal_bandpassed_mv():
    fs = 250
    t = np.arange(20 * fs) / fs
    x = 1024 + 200 * (2.0 + 0.5 * np.sin(2 * np.pi * 8 * t) + 0.5 * np.sin(2 * np.pi * 110 * t))
    sp = StreamProcessor(fs, None, adc_zero=1024, adc_gain=200.0)
    mv = np.array([v for o in stream(sp, x) if o.signal for v in o.signal["mv"]])
    tail = mv[10 * fs:]
    assert abs(tail.mean()) < 0.01                               # 2 mV offset removed
    assert np.ptp(tail) == pytest.approx(1.0, abs=0.1)           # 8 Hz kept at 1 mV p-p, 110 Hz removed
    assert np.abs(mv[:fs]).max() < 0.6                           # no start-up step from the offset


def test_adc_units_and_mv_give_identical_beats(fake_clf):
    fs = 360
    times, x = regular(fs, 30.0)
    a = collect(stream(StreamProcessor(fs, fake_clf), x), "beats")
    b = collect(stream(StreamProcessor(fs, fake_clf, adc_zero=512, adc_gain=100.0), 512 + 100.0 * x), "beats")
    assert [(q["sample"], q["label"]) for q in a] == [(q["sample"], q["label"]) for q in b]


# ------------------------------------------------- exactly once, in order
@pytest.mark.parametrize("frame", [7, 36, 180, 360, 5000])
def test_beats_are_emitted_once_in_order_whatever_the_framing(fake_clf, frame):
    fs = 360
    rng = np.random.default_rng(5)
    times = np.cumsum(rng.uniform(0.45, 1.2, 80)) + 0.3
    x = synth_ecg(times, fs, times[-1] + 1.0, wide=set(range(5, 80, 7)))
    outs = stream(StreamProcessor(fs, fake_clf), x, frame=frame)
    beats = collect(outs, "beats")
    samples = [b["sample"] for b in beats]
    assert samples == sorted(samples) and len(set(samples)) == len(samples)
    assert detected_ok(beats, times, fs)
    assert min(np.diff(samples)) > 0.2 * fs
    ends = [w["end_s"] for w in collect(outs, "windows")]
    assert ends == sorted(set(ends))


def test_framing_does_not_change_the_result(fake_clf):
    fs = 360
    times, x = regular(fs, 45.0)
    x = synth_ecg(times, fs, 45.0, wide={10, 11, 12, 30})
    a = stream(StreamProcessor(fs, fake_clf), x, frame=36)
    b = stream(StreamProcessor(fs, fake_clf), x, frame=360)
    assert collect(a, "beats") == collect(b, "beats")
    strip = lambda ws: [{k: v for k, v in w.items()} for w in ws]
    assert strip(collect(a, "windows")) == strip(collect(b, "windows"))


def test_beat_is_emitted_about_one_beat_after_its_r_peak():
    fs = 250
    times, x = regular(fs, 60.0)
    sp = StreamProcessor(fs, None)
    seen = {}
    for i in range(0, len(x), 25):
        for b in sp.push(x[i:i + 25], i).beats:
            seen[b["sample"]] = (i + 25) / fs - b["time_s"]
    lat = np.array(list(seen.values()))
    assert len(lat) >= len(times) - 2
    assert lat[0] < 4.0                                          # first beats wait for the 4 s warm-up
    assert 0.8 < np.median(lat) < 1.6 and lat[5:].max() < 2.0    # then: RR 0.8 s + guards + cadence
    assert sp.stats()["latency_s"]["median"] == pytest.approx(np.median(lat), abs=0.05)


def test_beat_before_a_long_pause_is_emitted_by_timeout_and_last_beat_by_flush():
    fs = 250
    times = [1.0, 1.8, 2.6, 3.4, 12.0, 12.8]
    x = synth_ecg(times, fs, 13.6)
    sp = StreamProcessor(fs, None)
    when = {}
    for i in range(0, len(x), 25):
        for b in sp.push(x[i:i + 25], i).beats:
            when[round(b["time_s"], 1)] = (i + 25) / fs
    assert 3.4 in when and when[3.4] < 3.4 + 3.6                 # not held back until the beat at 12 s
    assert 12.8 not in when
    assert [round(b["time_s"], 1) for b in sp.flush().beats] == [12.8]


# ------------------------------------------------------------------- parity
@pytest.mark.parametrize("fs", [125, 250, 360])
def test_streaming_labels_match_offline_classify(fake_clf, fs, report):
    rng = np.random.default_rng(11)
    times = np.cumsum(rng.uniform(0.6, 1.0, 150)) + 0.4
    wide = set(rng.choice(150, 30, replace=False).tolist())
    x = synth_ecg(times, fs, times[-1] + 1.0, wide=wide, noise=0.02)
    sp = StreamProcessor(fs, fake_clf)
    beats = collect(stream(sp, x), "beats")
    ag = offline_agreement(fake_clf, x, fs, beats)
    report(f"synthetic {fs} Hz, fake model: streaming-vs-offline agreement {ag['agreement']:.4f} "
           f"on {ag['matched']}/{ag['offline']} beats; labels {sp.stats()['labels']}")
    assert ag["coverage"] >= 0.99 and ag["agreement"] >= 0.98
    labels = sp.stats()["labels"]
    assert labels["V"] >= 20 and labels["N"] >= 80             # the fake model is not a constant


def test_class_bias_and_unknown_threshold_are_applied(fake_model_dir, tmp_path):
    fs = 250
    times, x = regular(fs, 30.0)
    cfg = json.loads((fake_model_dir / "model_config.json").read_text())

    def run(**override):
        d = tmp_path / "-".join(override) if override else tmp_path / "base"
        d.mkdir()
        (d / "beat_model_ts.pt").write_bytes((fake_model_dir / "beat_model_ts.pt").read_bytes())
        (d / "model_config.json").write_text(json.dumps({**cfg, **override}))
        return collect(stream(StreamProcessor(fs, load_classifier(d)), x), "beats")[2:-1]

    assert {b["label"] for b in run()} == {"N"}
    assert {b["label"] for b in run(unknown_threshold=1.01)} == {"Unknown"}
    biased = run(class_bias=[0, 0, 0, 50])
    assert {b["label"] for b in biased} == {"Unknown"}           # argmax moves to F, whose probability is tiny
    assert all(b["confidence"] == b["probs"]["F"] < 0.5 for b in biased)


# -------------------------------------------------------------- ground truth
def test_replay_annotations_become_aami_truth():
    fs = 360
    times, x = regular(fs, 20.0)
    symbols = ["N", "L", "A", "V", "E", "F", "/", "Q", "j", "S"]
    ann = [{"sample": int(t * fs) + (20 if i % 2 else -20), "symbol": symbols[i % 10]} for i, t in enumerate(times)]
    ann += [{"sample": int(times[3] * fs) + 2, "symbol": "+"}, {"sample": int(times[5] * fs), "symbol": "~"}]
    ann[12] = {"sample": int(times[12] * fs) + 80, "symbol": "V"}      # 222 ms away: outside +-150 ms
    beats = collect(stream(StreamProcessor(fs, None), x, annotations=ann), "beats")
    truth = [b["truth"] for b in beats]
    expect = [{"N": "N", "L": "N", "A": "S", "V": "V", "E": "V", "F": "F", "/": None, "Q": None, "j": "N",
               "S": "S"}[symbols[i % 10]] for i in range(len(times))]
    expect[12] = None
    assert truth == expect


# ---------------------------------------------------------------------- gaps
def test_gap_is_recorded_never_bridged(fake_clf):
    fs = 360
    times, x = regular(fs, 60.0)
    cut_a, cut_b = int(21.65 * fs), int(27.95 * fs)                      # 6.3 s lost
    sp = StreamProcessor(fs, fake_clf)
    outs = stream(sp, x[:cut_a], flush=False)
    outs += stream(sp, x[:cut_a][-36:], t0=cut_a - 36, flush=False)    # duplicate frame: ignored
    outs += stream(sp, x[cut_b:], t0=cut_b)
    assert collect(outs, "gaps") == [{"at_sample": cut_a, "missing": cut_b - cut_a}]
    q = collect(outs, "quality")
    assert q[0]["state"] == "gap" and "not interpolated" in q[0]["detail"] and q[-1]["state"] == "ok"
    beats = collect(outs, "beats")
    ts = np.array([b["time_s"] for b in beats])
    assert not ((ts > 21.65) & (ts < 27.95)).any()
    keep = times[(times < 21.65 - 0.1) | (times > 27.95 + 0.1)]
    assert detected_ok(beats, keep, fs)
    assert len(set(b["sample"] for b in beats)) == len(beats) and list(ts) == sorted(ts)
    windows = collect(outs, "windows")
    assert [w["start_s"] for w in windows] == [0, 10, 20, 30, 40, 50]
    rules = {f["rule_id"] for w in windows for f in w["findings"]}
    assert not rules & {"PAUSE", "PAUSE_CRITICAL", "BRADY", "BRADY_SEVERE"}
    assert collect(outs, "alerts") == []
    sig = [o.signal for o in outs if o.signal]
    assert sum(len(s["mv"]) for s in sig) == len(x) - (cut_b - cut_a)
    assert sp.stats()["gaps"] == 1


def test_long_gap_skips_empty_windows():
    fs = 250
    times, x = regular(fs, 25.0)
    sp = StreamProcessor(fs, None)
    outs = stream(sp, x, flush=False) + stream(sp, x, t0=int(3625 * fs))
    assert [w["start_s"] for w in collect(outs, "windows")] == [0, 10, 20, 3620, 3630, 3640]


def test_overlapping_frame_keeps_only_new_samples():
    fs = 250
    times, x = regular(fs, 20.0)
    sp = StreamProcessor(fs, None)
    outs = stream(sp, x[:2500], flush=False)
    out = sp.push(x[2400:2600], 2400)                                   # 100 samples already seen
    assert out.signal["t0"] == 2500 and len(out.signal["mv"]) == 100 and out.gaps == []
    outs += [out] + stream(sp, x[2600:], t0=2600)
    assert detected_ok(collect(outs, "beats"), times, fs)


def test_nan_samples_do_not_poison_the_stream():
    fs = 250
    times, x = regular(fs, 20.0)
    x[1000:1003] = np.nan
    outs = stream(StreamProcessor(fs, None), x)
    assert all(np.isfinite(o.signal["mv"]).all() for o in outs if o.signal)
    assert detected_ok(collect(outs, "beats"), times, fs)


# ------------------------------------------------- quality gating and alerts
def slow_rhythm(fs, dur=60.0):
    times = np.arange(0.7, dur - 0.5, 1.75)                             # 34 bpm
    return times, synth_ecg(times, fs, dur)


def test_severe_bradycardia_alert_needs_two_windows_and_carries_evidence():
    fs = 360
    times, x = slow_rhythm(fs)
    sp = StreamProcessor(fs, None, adc_zero=1024, adc_gain=200.0)
    outs = stream(sp, 1024 + 200 * x)
    alerts = collect(outs, "alerts")
    assert [(a["rule_id"], a["tier"]) for a in alerts] == [("BRADY_SEVERE", "red")]      # one per episode
    a = alerts[0]
    assert set(a) == {"rule_id", "tier", "title", "detail", "evidence"} and "34 bpm" in a["detail"]
    ev = a["evidence"]
    assert ev["window"]["start_s"] == 10.0 and ev["window"]["end_s"] == 20.0             # 2nd window, not 1st
    assert ev["rule_version"] == RULE_VERSION and ev["model_version"] is None and ev["reason"] == "new"
    assert ev["finding"]["rule_id"] == "BRADY_SEVERE"
    assert ev["fs"] == 250 and ev["end_s"] - ev["start_s"] == pytest.approx(10.0)
    assert len(ev["strip_mv"]) == 2500 and 0.5 < max(ev["strip_mv"]) < 1.5              # mV, <= 250 Hz
    assert len(ev["beats"]) in (5, 6, 7) and all("probs" in b for b in ev["beats"])
    assert all(ev["start_s"] <= b["time_s"] <= ev["end_s"] for b in ev["beats"])
    tiers = [w["risk"]["tier"] for w in collect(outs, "windows")]
    assert tiers[0] == "red"                                            # risk shows at once; the alert waits
    json.dumps(alerts)


def test_lead_off_suppresses_clinical_alerts_and_raises_a_device_task():
    fs = 250
    times, x = slow_rhythm(fs)
    sp = StreamProcessor(fs, None)
    outs = stream(sp, x[:20 * fs], lead_off=True, flush=False)
    assert collect(outs, "alerts") == []
    q = collect(outs, "quality")
    assert q[0]["state"] == "lead_off" and outs[0].quality == q[:1]     # raised by the very first frame
    tasks = collect(outs, "tasks")
    assert [t["rule_id"] for t in tasks] == ["LEAD_OFF"] and tasks[0]["kind"] == "device_task"
    assert all(o.signal["lead_off"] for o in outs)
    held = [w["risk"] for w in collect(outs, "windows")]
    assert held and all(r["tier"] == "green" and r["factors"][0]["rule_id"] == "LOW_SQI" for r in held)
    # electrodes back: clean windows are trusted again, and the alert still needs two of them
    outs2 = stream(sp, x[20 * fs:], t0=20 * fs)
    states = [s["state"] for s in collect(outs2, "quality")]
    assert states[0] == "low_sqi" and states[-1] == "ok"
    alerts = collect(outs2, "alerts")
    assert [a["rule_id"] for a in alerts] == ["BRADY_SEVERE"]
    assert alerts[0]["evidence"]["window"]["start_s"] >= 30.0
    assert collect(outs2, "tasks") == []


def test_low_sqi_suppresses_alerts_and_raises_low_sqi_task():
    fs = 250
    times, x = slow_rhythm(fs, 40.0)
    noise = np.random.default_rng(2).normal(0, 1.0, x.size) + 0.8 * np.sin(2 * np.pi * 50 * np.arange(x.size) / fs)
    outs = stream(StreamProcessor(fs, None), x + noise)
    windows = collect(outs, "windows")
    assert all(w["sqi"] < 0.5 for w in windows)
    assert collect(outs, "alerts") == []
    assert [t["rule_id"] for t in collect(outs, "tasks")] == ["LOW_SQI"]
    assert [q["state"] for q in collect(outs, "quality")] == ["low_sqi"]


def test_flat_line_is_a_device_problem_not_a_pause():
    fs = 250
    times, x = regular(fs, 12.0)
    x = np.r_[x, np.full(18 * fs, x[-1])]                               # cable pulled: dead flat, no lead-off flag
    outs = stream(StreamProcessor(fs, None), x)
    assert collect(outs, "alerts") == []
    assert "LOW_SQI" in [t["rule_id"] for t in collect(outs, "tasks")]


def test_pause_over_3_s_alerts_immediately():
    fs = 250
    times = np.r_[np.arange(0.5, 13.0, 0.8), np.arange(17.4, 30.0, 0.8)]   # 4.7 s without a beat from 12.5 s
    x = synth_ecg(times, fs, 30.0)
    sp = StreamProcessor(fs, None)
    raised = []
    for i in range(0, len(x), 25):
        for a in sp.push(x[i:i + 25], i).alerts:
            raised.append(((i + 25) / fs, a))
    crit = [(t, a) for t, a in raised if a["rule_id"] == "PAUSE_CRITICAL"]
    assert len(crit) == 1
    t, a = crit[0]
    assert a["tier"] == "red" and 15.5 < t < 17.0        # while the pause is still going on, before the window ends
    assert a["evidence"]["finding"]["start_s"] == pytest.approx(12.5, abs=0.05)
    assert len(a["evidence"]["strip_mv"]) > 5 * fs
    ws = [w for o in [sp.flush()] for w in o.windows]
    assert sp.stats()["alerts"] == len(raised)


def test_pause_with_undetected_beats_inside_is_not_a_pause():
    fs = 250
    times = np.arange(0.5, 40.0, 0.8)
    amp = np.ones(len(times))
    x = synth_ecg(times, fs, 40.0)
    small = synth_ecg(times[20:25], fs, 40.0, noise=0.0)                # beats 20-24 shrink to 45 %: missed
    x = x - 0.55 * small
    outs = stream(StreamProcessor(fs, None), x)
    ts = [b["time_s"] for b in collect(outs, "beats")]
    assert max(np.diff(ts)) > 3.0                                       # the detector really skipped them
    rules = {f["rule_id"] for w in collect(outs, "windows") for f in w["findings"]}
    assert not rules & {"PAUSE", "PAUSE_CRITICAL"} and collect(outs, "alerts") == []
    assert min(w["sqi"] for w in collect(outs, "windows")) < 0.97       # ... and it costs signal quality


def test_v_run_alert_end_to_end_with_model(fake_clf):
    fs = 360
    times = np.arange(0.5, 60.0, 0.8)
    wide = set(range(8, 12)) | set(range(20, 24)) | {40, 41}            # V runs at ~7 s and ~16.5 s, couplet later
    x = synth_ecg(times, fs, 60.5, wide=wide)
    sp = StreamProcessor(fs, fake_clf)
    outs = stream(sp, x)
    labels = [b["label"] for b in collect(outs, "beats")]
    assert [i for i, l in enumerate(labels) if l == "V"] == sorted(wide)
    windows = collect(outs, "windows")
    assert [f["rule_id"] for f in windows[0]["findings"]][0] == "V_RUN" and windows[0]["risk"]["tier"] == "orange"
    alerts = [(a["rule_id"], a["tier"], a["evidence"]["window"]["start_s"]) for a in collect(outs, "alerts")]
    assert ("V_RUN", "orange", 10.0) in alerts                          # confirmed by the second window
    assert ("V_COUPLET", "amber", 30.0) in alerts
    assert len([a for a in alerts if a[0] == "V_RUN"]) == 1
    ev = [a for a in collect(outs, "alerts") if a["rule_id"] == "V_RUN"][0]["evidence"]
    assert ev["model_version"] == "fake-test-model" and {b["label"] for b in ev["beats"]} == {"N", "V"}
    tiers = [w["risk"]["tier"] for w in windows]
    assert tiers[:2] == ["orange", "orange"] and tiers[-1] in ("amber", "green")     # decays only after clean windows
    assert tiers.index("amber") >= 4


def test_org_rules_config_reaches_the_engines():
    fs = 250
    times, x = regular(fs, 30.0)                                        # 75 bpm
    sp = StreamProcessor(fs, None, rules_config={"tachy_bpm": 70})
    outs = stream(sp, x)
    assert sp.rule_version.startswith("rules-1.0.0+")
    assert [a["rule_id"] for a in collect(outs, "alerts")] == ["TACHY"]
    assert collect(outs, "windows")[0]["rule_version"] == sp.rule_version
