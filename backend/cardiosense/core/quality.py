"""Signal quality index (SQI, 0-1 per rhythm window) and the causal display filter.

SQI = coverage x (1 - lead-off fraction) x prod(1 - w_i * penalty_i) over
flat-line, saturation, high-frequency noise, baseline wander, Unknown-beat
ratio and implausible RR intervals. Rule based and deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi

from .config import RulesConfig


class CausalFilter:
    """Stateful `sosfilt`; the state is primed with the first sample so a DC offset causes no step transient."""

    def __init__(self, sos):
        self.sos = sos
        self._zi0 = sosfilt_zi(sos)
        self._zi = None

    def reset(self) -> None:
        self._zi = None

    def __call__(self, x: np.ndarray) -> np.ndarray:
        if self._zi is None:
            self._zi = self._zi0 * x[0]
        y, self._zi = sosfilt(self.sos, x, zi=self._zi)
        return y


def display_filter(fs: float) -> CausalFilter:
    """Causal 0.5-40 Hz band-pass at the device rate (upper edge clamped below Nyquist for slow devices)."""
    return CausalFilter(butter(2, [0.5, min(40.0, 0.45 * fs)], btype="band", fs=fs, output="sos"))


def _ramp(v: float, ok: float, bad: float) -> float:
    return float(np.clip((v - ok) / (bad - ok), 0.0, 1.0))


def flat_fraction(x: np.ndarray, fs: float, ptp_mv: float) -> float:
    """Fraction of 2 s blocks whose peak-to-peak amplitude is below `ptp_mv` (dead / disconnected signal)."""
    blk = max(1, int(2 * fs))
    if len(x) < blk:
        return float(np.ptp(x) <= ptp_mv) if len(x) else 0.0
    nb = len(x) // blk
    b = x[:nb * blk].reshape(nb, blk)
    return float(np.mean(b.max(1) - b.min(1) <= ptp_mv))


def rail_fraction(x: np.ndarray, min_run: int) -> float:
    """Fraction of samples sitting in plateaus (>= min_run identical samples) at the window's extreme values."""
    hi, lo = x.max(), x.min()
    if hi <= lo:
        return 0.0
    at = np.r_[False, (x == hi) | (x == lo), False]
    edges = np.flatnonzero(at[1:] != at[:-1])
    runs = edges[1::2] - edges[::2]
    return float(runs[runs >= min_run].sum() / len(x))


@dataclass
class QualityResult:
    sqi: float
    state: str                    # ok | lead_off | low_sqi
    detail: str
    coverage: float
    components: dict = field(default_factory=dict)

    @property
    def suppressed(self) -> bool:
        return self.state != "ok"


class QualityMonitor:
    def __init__(self, fs: float, cfg: RulesConfig):
        self.fs, self.cfg = float(fs), cfg
        self._win = int(round(cfg.window_s * fs))
        self._hf = CausalFilter(butter(2, 45.0, btype="high", fs=fs, output="sos")) if fs >= 120 else None
        self._bw = CausalFilter(butter(2, 0.5, btype="low", fs=fs, output="sos"))
        self._acc: dict[int, dict] = {}

    def reset_filters(self) -> None:
        if self._hf:
            self._hf.reset()
        self._bw.reset()

    def has(self, k: int) -> bool:
        return k in self._acc

    def first_window(self) -> int | None:
        return min(self._acc) if self._acc else None

    def add(self, t0: int, raw_mv: np.ndarray, band_mv: np.ndarray, lead_off: bool) -> None:
        hf = self._hf(raw_mv) if self._hf else None
        bw = self._bw(raw_mv)
        i, n = 0, len(raw_mv)
        while i < n:                                   # split at window boundaries
            k = (t0 + i) // self._win
            j = min(n, (k + 1) * self._win - t0)
            a = self._acc.setdefault(k, {"raw": [], "band": [], "hf": [], "bw": [], "lo": 0, "n": 0})
            a["raw"].append(raw_mv[i:j]); a["band"].append(band_mv[i:j]); a["bw"].append(bw[i:j])
            if hf is not None:
                a["hf"].append(hf[i:j])
            a["n"] += j - i
            a["lo"] += (j - i) if lead_off else 0
            i = j

    def close(self, k: int, n_beats: int = 0, n_unknown: int = 0, rr: list | None = None,
              use_unknown: bool = True, rr_bad: int = 0) -> QualityResult | None:
        """Score window `k` and drop its accumulators. None if the window holds no signal (inside a gap)."""
        a = self._acc.pop(k, None)
        for old in [j for j in self._acc if j < k]:
            del self._acc[old]
        if a is None or a["n"] == 0:
            return None
        cfg, w = self.cfg, self.cfg.sqi_weights
        raw, band = np.concatenate(a["raw"]), np.concatenate(a["band"])
        coverage = min(1.0, a["n"] / self._win)
        lo_frac = a["lo"] / a["n"]
        flat = flat_fraction(raw, self.fs, cfg.flat_ptp_mv)
        sat = 0.0 if flat >= 1.0 else rail_fraction(raw, max(4, int(0.04 * self.fs)))
        band_rms = float(np.sqrt(np.mean(band ** 2))) + 1e-9
        amp = float(np.percentile(band, 99.5) - np.percentile(band, 0.5)) + 1e-9
        hf_ratio = float(np.sqrt(np.mean(np.concatenate(a["hf"]) ** 2))) / band_rms if a["hf"] else 0.0
        bw_ratio = float(np.ptp(np.concatenate(a["bw"]))) / amp
        rr = rr or []
        comp = {
            "flat": flat,
            "saturation": min(1.0, sat / 0.05),
            "hf": 0.0 if flat >= 1.0 else _ramp(hf_ratio, cfg.hf_ratio_ok, cfg.hf_ratio_bad),
            "baseline": 0.0 if flat >= 1.0 else _ramp(bw_ratio, cfg.bw_ratio_ok, cfg.bw_ratio_bad),
            "unknown": n_unknown / n_beats if use_unknown and n_beats >= 3 else 0.0,
            # implausibly short intervals + long ones that turned out to hide undetected beats
            "rr": min(1.0, (sum(1 for v in rr if v < cfg.rr_implausible_s) + rr_bad) / len(rr)) if rr else 0.0,
        }
        sqi = (0.5 + 0.5 * coverage) * (1.0 - lo_frac)
        for name, p in comp.items():
            sqi *= 1.0 - min(1.0, w.get(name, 1.0) * p)
        sqi = round(float(np.clip(sqi, 0.0, 1.0)), 3)
        comp.update(lead_off=lo_frac, hf_ratio=hf_ratio, bw_ratio=bw_ratio)

        if lo_frac >= cfg.lead_off_frac:
            state, detail = "lead_off", f"Electrode lead-off for {lo_frac:.0%} of the window — check electrodes"
        elif sqi < cfg.sqi_low:
            texts = {"flat": "flat line", "saturation": "amplifier saturation", "hf": "high-frequency noise",
                     "baseline": "baseline wander", "unknown": "many unclassifiable beats",
                     "rr": "implausible beat intervals"}
            worst = sorted((n for n in texts if comp[n] > 0.15), key=lambda n: -w.get(n, 1.0) * comp[n])
            state = "low_sqi"
            detail = f"Low signal quality (SQI {sqi:.2f}): " + (", ".join(texts[n] for n in worst[:3]) or "noisy signal")
        else:
            state, detail = "ok", f"Signal quality OK (SQI {sqi:.2f})"
        return QualityResult(sqi, state, detail, coverage, comp)
