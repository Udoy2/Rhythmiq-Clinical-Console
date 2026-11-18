"""CardioSense AI beat classifier.

Shared by the training notebook and the FastAPI backend so preprocessing is
identical in both places. Requires numpy, scipy and torch.
"""
import json
from math import gcd

import numpy as np
from scipy.signal import butter, find_peaks, resample_poly, sosfiltfilt

CLASSES = ["N", "S", "V", "F"]
CLASS_NAMES = {"N": "Normal", "S": "Supraventricular ectopic",
               "V": "Ventricular ectopic", "F": "Fusion"}


def preprocess(x, fs_in, fs_out=250, band=(0.5, 40.0), order=3):
    x = np.asarray(x, dtype=np.float64)
    if fs_in != fs_out:
        g = gcd(int(fs_in), int(fs_out))
        x = resample_poly(x, int(fs_out) // g, int(fs_in) // g)
    sos = butter(order, list(band), btype="band", fs=fs_out, output="sos")
    return sosfiltfilt(sos, x)


def causal_mean(v, n):
    c = np.cumsum(np.r_[0.0, v])
    i = np.arange(len(v))
    s = np.maximum(0, i - n + 1)
    return (c[i + 1] - c[s]) / (i + 1 - s)


def rr_features(r, fs=250):
    rr = np.diff(r) / fs
    pre = np.r_[rr[0], rr]
    post = np.r_[rr, rr[-1]]
    loc10, loc100 = causal_mean(pre, 10), causal_mean(pre, 100)
    f = np.stack([pre, post, loc10, pre / loc10, post / loc10, pre / post, pre / loc100], 1)
    return np.log(np.clip(f, 1e-2, 10)).astype(np.float32)


def zscore(W):
    return (W - np.median(W, 1, keepdims=True)) / (W.std(1, keepdims=True) + 1e-6)


def running_template(Z, n_prev=20):
    T = np.empty_like(Z)
    T[0] = Z[0]
    for k in range(1, len(Z)):
        T[k] = np.median(Z[max(0, k - n_prev):k], 0)
    return T


def detect_r_peaks(x, fs=250):
    sos = butter(2, [5, 18], btype="band", fs=fs, output="sos")
    y = sosfiltfilt(sos, x)
    e = np.gradient(y) ** 2
    w = max(1, int(round(0.12 * fs)))
    integ = np.convolve(e, np.ones(w) / w, mode="same")
    blk = int(5 * fs)
    thr = np.empty_like(integ)
    for s in range(0, len(integ), blk):
        thr[s:s + blk] = 0.3 * np.percentile(integ[max(0, s - 2 * blk):s + 3 * blk], 98)
    cand, _ = find_peaks(integ, distance=int(0.25 * fs))
    cand = cand[integ[cand] > thr[cand]]
    half = int(0.08 * fs)
    r = [max(0, p - half) + int(np.argmax(np.abs(x[max(0, p - half):p + half + 1]))) for p in cand]
    keep = []
    for i in sorted(set(r)):
        if not keep or i - keep[-1] > int(0.2 * fs):
            keep.append(i)
        elif abs(x[i]) > abs(x[keep[-1]]):
            keep[-1] = i
    return np.array(keep, dtype=int)


def _model_norm(a):
    # matches torch.median (lower middle) and torch.std (ddof=1) used in training
    k = (a.shape[1] - 1) // 2
    med = np.partition(a, k, axis=1)[:, k:k + 1]
    return (a - med) / (a.std(1, ddof=1, keepdims=True) + 1e-6)


def build_inputs(x, r, cfg):
    pre, post, margin, fs = cfg["pre"], cfg["post"], cfg["margin"], cfg["fs"]
    L = pre + post
    rr = rr_features(r, fs)
    ok = (r - pre - margin >= 0) & (r + post + margin <= len(x))
    if not ok.any():
        return None, None, ok
    W = x[r[ok, None] + np.arange(-pre - margin, post + margin)[None, :]].astype(np.float32)
    T = running_template(zscore(W), cfg["template_prev_beats"])
    xc = _model_norm(W[:, margin:margin + L])
    tc = _model_norm(T[:, margin:margin + L])
    return np.stack([xc, xc - tc], 1).astype(np.float32), rr[ok], ok


class BeatClassifier:
    def __init__(self, model_path, config_path, device="cpu"):
        import torch
        self._torch = torch
        with open(config_path) as f:
            self.cfg = json.load(f)
        self.device = device
        self.model = torch.jit.load(model_path, map_location=device).eval()

    def predict_inputs(self, beats, rr, batch=2048):
        torch = self._torch
        out = []
        with torch.no_grad():
            for i in range(0, len(beats), batch):
                b = torch.from_numpy(beats[i:i + batch]).to(self.device)
                f = torch.from_numpy(rr[i:i + batch]).to(self.device)
                out.append(self.model(b, f).cpu().numpy())
        return np.concatenate(out) if out else np.zeros((0, len(CLASSES)), np.float32)

    def classify(self, raw, fs_in):
        cfg = self.cfg
        x = preprocess(raw, fs_in, cfg["fs"], tuple(cfg["bandpass_hz"]), cfg["filter_order"])
        r = detect_r_peaks(x, cfg["fs"])
        if len(r) < 2:
            return []
        beats, rr, ok = build_inputs(x, r, cfg)
        if beats is None:
            return []
        P = self.predict_inputs(beats, rr)
        idx = (np.log(P + 1e-9) + np.asarray(cfg["class_bias"])).argmax(1)
        results = []
        for pos, p, k in zip(r[ok], P, idx):
            conf = float(p[k])
            results.append({
                "sample": int(pos),
                "time_s": float(pos / cfg["fs"]),
                "label": CLASSES[k] if conf >= cfg["unknown_threshold"] else "Unknown",
                "confidence": conf,
                "probs": {c: float(v) for c, v in zip(CLASSES, p)},
                "model_version": cfg["model_version"],
            })
        return results
