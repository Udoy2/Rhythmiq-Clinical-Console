"""Signal quality index: each component, and the lead-off / low-SQI states."""
import numpy as np
import pytest

from cardiosense.core import QualityMonitor, RulesConfig
from cardiosense.core.quality import display_filter, flat_fraction, rail_fraction
from core_helpers import synth_ecg

FS = 250


def score(x, lead_off=False, fs=FS, **kw):
    q = QualityMonitor(fs, RulesConfig())
    band = display_filter(fs)(x)
    step = fs // 10
    for i in range(0, len(x), step):
        lo = lead_off(i / fs) if callable(lead_off) else lead_off
        q.add(i, x[i:i + step], band[i:i + step], lo)
    return q.close(0, **kw)


def clean(fs=FS, noise=0.01):
    return synth_ecg(np.arange(0.4, 10, 0.8), fs, 10.0, noise=noise)


def test_clean_signal_scores_high():
    for fs in (60, 125, 250, 360):
        r = score(clean(fs), fs=fs, n_beats=12, n_unknown=0, rr=[0.8] * 11)
        assert r.sqi > 0.9 and r.state == "ok" and not r.suppressed and r.coverage == 1.0, fs


def test_flat_line_is_zero():
    r = score(np.full(10 * FS, 0.37))
    assert r.sqi == 0.0 and r.state == "low_sqi" and "flat line" in r.detail
    half = clean()
    half[5 * FS:] = half[5 * FS]
    assert 0.3 < score(half).sqi < 0.7


def test_lead_off_state_wins():
    r = score(clean(), lead_off=True)
    assert r.state == "lead_off" and r.sqi == 0.0 and r.suppressed
    r = score(clean(), lead_off=lambda t: t >= 9.0)            # 10 % of the window
    assert r.state == "lead_off" and r.sqi == pytest.approx(0.9, abs=0.05)
    r = score(clean(), lead_off=lambda t: t < 0.2)             # 2 %: below lead_off_frac
    assert r.state == "ok"


def test_saturation():
    x = np.clip(clean() * 3, -0.2, 0.3)                        # every QRS clipped at the rail
    assert rail_fraction(x, 10) > 0.01
    r = score(x)
    assert r.components["saturation"] > 0.3 and r.sqi < score(clean()).sqi
    assert rail_fraction(clean(), 10) == 0.0


def test_high_frequency_noise_and_baseline_wander_lower_sqi():
    t = np.arange(10 * FS) / FS
    noisy = clean() + 0.6 * np.sin(2 * np.pi * 60 * t) + np.random.default_rng(3).normal(0, 0.3, t.size)
    r = score(noisy)
    assert r.components["hf"] > 0.5 and r.sqi < 0.7
    wander = clean() + 4.0 * np.sin(2 * np.pi * 0.15 * t)
    r = score(wander)
    assert r.components["baseline"] > 0.3 and r.sqi < 0.9
    assert score(clean() + 0.1 * np.sin(2 * np.pi * 0.2 * t)).components["baseline"] == 0.0


def test_unknown_ratio_and_implausible_rr():
    base = score(clean(), n_beats=12, n_unknown=0, rr=[0.8] * 11).sqi
    r = score(clean(), n_beats=12, n_unknown=9, rr=[0.8] * 11)
    assert r.sqi == pytest.approx(base * (1 - 0.8 * 0.75), abs=0.01) and r.state == "low_sqi"
    assert "unclassifiable" in r.detail
    # rules-only mode: every beat is Unknown by construction, which says nothing about the signal
    assert score(clean(), n_beats=12, n_unknown=12, rr=[0.8] * 11, use_unknown=False).sqi == base
    r = score(clean(), n_beats=12, n_unknown=0, rr=[0.8] * 6 + [0.21] * 6)
    assert r.components["rr"] == 0.5 and r.sqi < base
    r = score(clean(), n_beats=12, n_unknown=0, rr=[0.8] * 10, rr_bad=2)      # long RR hiding missed beats
    assert r.components["rr"] == pytest.approx(0.2)


def test_partial_window_and_empty_window():
    q = QualityMonitor(FS, RulesConfig())
    assert q.close(3) is None
    x = clean()[:3 * FS]
    q.add(0, x, x, False)
    r = q.close(0)
    assert r.coverage == pytest.approx(0.3) and 0.5 < r.sqi < 0.8


def test_frames_are_split_at_window_boundaries():
    q = QualityMonitor(FS, RulesConfig())
    x = np.r_[clean(), np.full(10 * FS, 0.1)]                  # one push covering two windows
    q.add(0, x, display_filter(FS)(x), False)
    assert q.has(0) and q.has(1)
    assert q.close(0).sqi > 0.9 and q.close(1).sqi == 0.0


def test_flat_fraction_helper():
    assert flat_fraction(np.zeros(1000), FS, 0.008) == 1.0
    assert flat_fraction(clean(), FS, 0.008) == 0.0
