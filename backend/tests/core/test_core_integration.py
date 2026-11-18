"""MIT-BIH records streamed in 100 ms frames through StreamProcessor (as the replay sender does).

Record 208 (MLII, 360 Hz, ADC zero 1024, gain 200) is the demo "critical" patient. Note on the
R-peak criterion: the offline detector `cardiosense_beats.detect_r_peaks`, which the core must
reproduce and may not change, itself finds only ~74 % of the annotated beats of record 208 (its
threshold follows the large V beats and skips the small normal ones). So on 208 the test asserts
*parity with the offline detector*; the absolute >= 0.97 sensitivity is asserted on record 100,
where the detector is in its normal operating range.
"""
import numpy as np
import pytest

from cardiosense.core import StreamProcessor
from cardiosense.core.bench import confusion, format_confusion, offline_agreement, r_peak_scores, replay
from cardiosense.core.model import cb

FS, ZERO, GAIN = 360, 1024, 200.0
FRAME_MS = 100.0


def run(record, real_clf, name, seconds, report):
    x, ann = record(name, seconds)
    sp = StreamProcessor(fs=FS, classifier=real_clf, adc_zero=ZERO, adc_gain=GAIN)
    res = replay(sp, x, ann, FRAME_MS)
    mv = (x - ZERO) / GAIN
    rp = r_peak_scores(res.beats, ann, FS)
    r_off = cb.detect_r_peaks(cb.preprocess(mv, FS), 250)
    off = r_peak_scores([{"sample": int(round(i * FS / 250))} for i in r_off], ann, FS)
    ag = offline_agreement(real_clf, mv, FS, res.beats) if real_clf is not None else None
    st, cpu = sp.stats(), res.cpu()
    tag = f"record {name}, {len(x) / FS:.0f} s"
    report(f"{tag}: R-peak sensitivity {rp['sensitivity']:.4f} +P {rp['ppv']:.4f} "
           f"(offline detector on the same signal: {off['sensitivity']:.4f} / {off['ppv']:.4f})")
    if ag:
        report(f"{tag}: streaming-vs-offline label agreement {ag['agreement']:.4f} on {ag['matched']} matched beats "
               f"(offline {ag['offline']}, coverage {ag['coverage']:.4f}); model {sp.model_version}")
    report(f"{tag}: beat latency median {st['latency_s']['median']:.2f} s, p95 {st['latency_s']['p95']:.2f} s, "
           f"max {st['latency_s']['max']:.2f} s")
    report(f"{tag}: CPU per push mean {cpu['mean']:.2f} ms, p50 {cpu['p50']:.2f} ms, p95 {cpu['p95']:.2f} ms, "
           f"max {cpu['max']:.2f} ms (frame period {FRAME_MS:.0f} ms); {res.realtime_factor:.0f}x real time")
    report(f"{tag}: labels {st['labels']}; alerts {[(a['rule_id'], a['tier']) for a in res.alerts]}")
    return x, ann, sp, res, rp, off, ag, cpu


def check_common(x, ann, sp, res, cpu, real_clf):
    samples = [b["sample"] for b in res.beats]
    assert samples == sorted(samples) and len(set(samples)) == len(samples)          # once, in order
    assert res.signal_samples == len(x) and res.gaps == []
    assert len(res.windows) == int(np.ceil(len(x) / FS / 10.0 - 0.1))                # one per 10 s
    assert all(0.0 <= w["sqi"] <= 1.0 and 0 <= w["risk"]["score"] <= 100 for w in res.windows)
    st = sp.stats()
    assert st["latency_s"]["median"] < 1.5                                           # ~ one beat after the R-peak
    # 100 ms frames: the mean cost must be a small fraction of the frame period, the worst case below budget
    assert cpu["mean"] < 0.15 * FRAME_MS and cpu["p50"] < 0.05 * FRAME_MS
    assert cpu["p95"] < FRAME_MS and cpu["max"] < 250.0
    assert res.realtime_factor > 5.0
    for a in res.alerts:
        ev = a["evidence"]
        assert a["rule_id"] not in ("LEAD_OFF", "LOW_SQI") and a["tier"] in ("amber", "orange", "red")
        assert ev["rule_version"] == sp.rule_version and ev["model_version"] == sp.model_version
        assert ev["fs"] <= 250 and 0 < len(ev["strip_mv"]) <= 2500 and ev["beats"]
        if a["rule_id"].startswith("S_"):
            assert a["tier"] == "amber"
    if real_clf is not None:
        with_truth = [b for b in res.beats if b["truth"]]
        assert len(with_truth) >= 0.95 * len(res.beats)                              # replay truth attached


def test_record_208_five_minutes(record, real_clf, report):
    x, ann, sp, res, rp, off, ag, cpu = run(record, real_clf, "208", 300, report)
    check_common(x, ann, sp, res, cpu, real_clf)
    assert rp["ppv"] >= 0.99
    assert rp["sensitivity"] >= off["sensitivity"] - 0.01            # parity with the (unchangeable) detector
    if ag is None:
        pytest.skip("no model in ml/models/cardiosense_beat_model: label agreement not measured")
    assert ag["coverage"] >= 0.97 and ag["agreement"] >= 0.98
    report("record 208, 300 s confusion (AAMI truth vs streaming label):\n" + format_confusion(confusion(res.beats)))
    # the demo patient: heavy ventricular ectopy must surface, and nothing may fire on undetected beats
    rules = {a["rule_id"] for a in res.alerts}
    assert "V_BURDEN" in rules and "V_COUPLET" in rules
    assert not rules & {"PAUSE", "PAUSE_CRITICAL"}                   # 208 has no true pause in this stretch
    assert max(w["risk"]["tier"] for w in res.windows[3:]) in ("orange", "red")


def test_record_100_three_minutes(record, real_clf, report):
    x, ann, sp, res, rp, off, ag, cpu = run(record, real_clf, "100", 180, report)
    check_common(x, ann, sp, res, cpu, real_clf)
    assert rp["sensitivity"] >= 0.97 and rp["ppv"] >= 0.99
    if ag is not None:
        assert ag["coverage"] >= 0.99 and ag["agreement"] >= 0.98
    # the stable patient stays quiet: nothing urgent, no pause / V findings
    assert all(a["tier"] == "amber" for a in res.alerts) and len(res.alerts) <= 1
    assert all(w["risk"]["tier"] in ("green", "amber") for w in res.windows)
    assert not {f["rule_id"] for w in res.windows for f in w["findings"]} & {"PAUSE", "PAUSE_CRITICAL", "V_RUN"}
    assert all(70 <= w["hr_bpm"] <= 85 for w in res.windows if w["hr_bpm"])


def test_rules_only_mode_on_real_signal(record, report):
    x, ann = record("100", 60)
    sp = StreamProcessor(fs=FS, classifier=None, adc_zero=ZERO, adc_gain=GAIN)
    res = replay(sp, x, ann, FRAME_MS)
    assert r_peak_scores(res.beats, ann, FS)["sensitivity"] >= 0.97
    assert {b["label"] for b in res.beats} == {"Unknown"} and sp.model_version is None
    assert all(w["sqi"] > 0.8 for w in res.windows) and all(w["hr_bpm"] for w in res.windows)
    assert {b["truth"] for b in res.beats} <= {"N", "S", "V", "F", None}


@pytest.mark.slow
def test_record_208_full_30_minutes(record, real_clf, report):
    x, ann, sp, res, rp, off, ag, cpu = run(record, real_clf, "208", None, report)
    check_common(x, ann, sp, res, cpu, real_clf)
    assert len(x) / FS > 1800 and res.realtime_factor > 5.0
    assert rp["ppv"] >= 0.99 and rp["sensitivity"] >= off["sensitivity"] - 0.01
    if ag is not None:
        # over the whole record ~1-2 % of the peaks differ from the offline run: the detector's threshold
        # looks 10-15 s ahead, which a live stream cannot; beats next to such a peak get other RR features
        assert ag["coverage"] >= 0.97 and ag["agreement"] >= 0.97
