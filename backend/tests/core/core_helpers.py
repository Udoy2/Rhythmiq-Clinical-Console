"""Helpers shared by the pipeline-core tests (unique module name: tests/ has no packages)."""
import numpy as np


def synth_ecg(beat_times, fs, duration_s, wide=(), amp=1.0, noise=0.01, seed=0):
    """mV signal with a narrow QRS + T wave at each beat time; indices in `wide` are broad, inverted beats."""
    t = np.arange(int(round(duration_s * fs))) / fs
    x = np.random.default_rng(seed).normal(0.0, noise, t.size)
    for i, bt in enumerate(beat_times):
        if i in wide:
            x += -1.3 * amp * np.exp(-0.5 * ((t - bt) / 0.035) ** 2) + 0.4 * amp * np.exp(-0.5 * ((t - bt - 0.30) / 0.06) ** 2)
        else:
            x += amp * np.exp(-0.5 * ((t - bt) / 0.012) ** 2) + 0.2 * amp * np.exp(-0.5 * ((t - bt - 0.25) / 0.05) ** 2)
    return x


def stream(sp, x, frame=None, t0=0, lead_off=False, annotations=None, flush=True):
    """Push `x` in frames; returns the list of PipelineOutputs (the flush output last)."""
    frame = frame or max(1, sp.fs // 10)
    outs = []
    for i in range(0, len(x), frame):
        ann = [a for a in (annotations or []) if t0 + i <= a["sample"] < t0 + i + frame] or None
        outs.append(sp.push(x[i:i + frame], t0 + i, lead_off, ann))
    if flush:
        outs.append(sp.flush())
    return outs


def collect(outs, name):
    return [item for o in outs for item in getattr(o, name)]
