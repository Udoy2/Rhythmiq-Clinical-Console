"""Streaming beat detection + classification that mirrors `BeatClassifier.classify`.

A rolling buffer of raw mV (device rate) is re-run through the *offline*
`preprocess` and `detect_r_peaks` at most every `proc_interval_s` of signal.
The buffer always starts on a 5 s boundary of the segment, so

* the polyphase resampler sees the same sample phase as a whole-record run
  (5 s is a multiple of the decimation factor for every integer rate), and
* the detector's 5 s threshold blocks fall on the same grid as offline.

A peak is finalised exactly once, when its post-window is clear of the filter
edge and the following peak is known (or a timeout passed). Template and RR
history live outside the buffer, so features equal the offline ones.
"""
from __future__ import annotations

from collections import deque
from math import gcd

import numpy as np

from .model import DEFAULT_MODEL_CFG, cb
from .types import BeatEvent

BLOCK_S = 5          # detect_r_peaks threshold block (s); buffer trims in these units
EDGE_PAD_S = 3.0     # mirror extension of the live edge before the zero-phase filter
WARMUP_S = 4.0       # signal needed in a new segment before its first beats are finalised
RR_HISTORY = 101     # previous peaks kept: enough for the 100-beat causal mean


class BeatStream:
    def __init__(self, fs: int, classifier=None, proc_interval_s: float = 0.5, peak_guard_s: float = 0.35,
                 filt_guard_s: float = 0.25, timeout_s: float = 2.5, keep_s: float = 25.0):
        self.fs = int(fs)
        self.clf = classifier
        self.cfg = {**DEFAULT_MODEL_CFG, **(classifier.cfg if classifier is not None else {})}
        self.F = F = int(self.cfg["fs"])
        self.pre, self.post, self.margin = (int(self.cfg[k]) for k in ("pre", "post", "margin"))
        self.band, self.order = tuple(self.cfg["bandpass_hz"]), int(self.cfg["filter_order"])
        self.bias = np.asarray(self.cfg["class_bias"], dtype=np.float64)
        self.thr = float(self.cfg["unknown_threshold"])
        self.classes = list(self.cfg["classes"])
        self.model_version = self.cfg["model_version"] if classifier is not None else None

        self.proc_step = max(1, int(round(proc_interval_s * self.fs)))
        self.peak_guard = int(round(peak_guard_s * F))
        self.filt_guard = int(round(filt_guard_s * F))
        self.timeout = int(round(timeout_s * F))
        self.refractory = int(0.2 * F)            # same minimum spacing as detect_r_peaks
        self._block = BLOCK_S * self.fs
        self._pad = int(EDGE_PAD_S * self.fs)
        self._warmup = int(WARMUP_S * self.fs)
        self._keep = int(np.ceil(keep_s / BLOCK_S)) * self._block
        self._buf = np.empty(self._keep + 2 * self._block + self.proc_step)
        self._tpl: deque = deque(maxlen=int(self.cfg["template_prev_beats"]))
        assert (self._block * F) % self.fs == 0 and self._block % (self.fs // gcd(self.fs, F)) == 0
        self.start_segment(0, 0)

    # ------------------------------------------------------------------ state
    def start_segment(self, seg_start: int, seg_id: int, keep_template: bool = False) -> None:
        """Begin a new contiguous segment at absolute device sample `seg_start` (stream start or after a gap)."""
        self.seg_start, self.seg_id = int(seg_start), seg_id
        self._n = 0                 # samples in the buffer
        self._buf_start = 0         # segment-relative device index of _buf[0] (multiple of 5 s)
        self._since_proc = 0
        self._last_r = None         # last finalised peak (segment-relative index at F Hz)
        self._r_hist: deque = deque(maxlen=RR_HISTORY)
        if not keep_template:
            self._tpl.clear()
        self.frontier = float(seg_start)   # abs device sample before which every beat has been emitted

    @property
    def end_sample(self) -> int:
        return self.seg_start + self._buf_start + self._n

    @property
    def due(self) -> bool:
        return self._since_proc >= self.proc_step

    def append(self, mv: np.ndarray) -> None:
        """Add raw mV samples; callers feed at most `proc_step` samples between `process` calls."""
        k = len(mv)
        if self._n + k > len(self._buf):
            raise ValueError("BeatStream.append: chunk too large, call process() between appends")
        self._buf[self._n:self._n + k] = mv
        self._n += k
        self._since_proc += k

    def _trim(self) -> None:
        while self._n > self._keep + self._block:
            self._buf[:self._n - self._block] = self._buf[self._block:self._n]
            self._n -= self._block
            self._buf_start += self._block

    def _to_device(self, r: float) -> float:
        return self.seg_start + r * self.fs / self.F

    # -------------------------------------------------------------- processing
    def process(self, final: bool = False) -> list[BeatEvent]:
        """Re-analyse the buffer and return newly finalised beats. `final=True` drains the segment
        (end of stream / before a gap): guards are dropped and the last beat is emitted too."""
        self._since_proc = 0
        F = self.F
        if self._n < self.fs or (not final and self._buf_start == 0 and self._n < self._warmup):
            # too little signal: the detector's adaptive threshold is meaningless on a near-empty buffer
            # (it would lock on to noise), so the first beats of a segment wait for WARMUP_S of signal
            if final:
                self.frontier = float(self.end_sample)
            return []
        x = self._preprocess(final)
        off = self._buf_start * F // self.fs
        r = cb.detect_r_peaks(x, F) + off
        n_end = off + len(x)
        horizon = n_end if final else n_end - self.peak_guard
        lo = -1 if self._last_r is None else self._last_r + self.refractory
        cand = r[(r > lo) & (r <= horizon)]

        ready = []                           # (peak, next_peak_or_None)
        frontier = horizon
        for i, rk in enumerate(cand):
            rk = int(rk)
            nxt = int(cand[i + 1]) if i + 1 < len(cand) else None
            if not final:
                if rk + self.post + self.margin + self.filt_guard > n_end or \
                        (nxt is None and horizon - rk < self.timeout):
                    frontier = rk
                    break
                if nxt is None:
                    nxt = horizon            # timed out: the post-RR is at least this long
            ready.append((rk, nxt))
        events = self._finalise(ready, x, off) if ready else []
        self.frontier = max(self.frontier, self._to_device(frontier))
        self._trim()
        return events

    def _preprocess(self, final: bool) -> np.ndarray:
        """Offline `preprocess` on the buffer. While the stream is live the buffer is extended by a mirror
        image of its last seconds first (and the extension discarded afterwards): the zero-phase filter's
        backward pass then starts from a realistic state instead of a cold one, which cuts the error near
        the live edge several-fold. At the true end of a segment (`final`) the call is exactly the offline one."""
        raw = self._buf[:self._n]
        if final:
            return cb.preprocess(raw, self.fs, self.F, self.band, self.order)
        pad = min(self._pad, self._n - 1)
        ext = np.concatenate([raw, raw[-2:-pad - 2:-1]])
        n_out = -(-self._n * self.F // self.fs)          # length resample_poly gives for the real samples
        return cb.preprocess(ext, self.fs, self.F, self.band, self.order)[:n_out]

    def _finalise(self, ready, x, off) -> list[BeatEvent]:
        F, pre, post, m = self.F, self.pre, self.post, self.margin
        L = pre + post
        rows, beats_in, rr_in = [], [], []
        for rk, nxt in ready:
            hist = list(self._r_hist)
            seq = hist + [rk] + ([nxt] if nxt is not None else [])
            feat = cb.rr_features(np.asarray(seq), F)[len(hist)] if len(seq) >= 2 else None
            i = rk - off
            if self.clf is not None and feat is not None and i - pre - m >= 0 and i + post + m <= len(x):
                W = x[i - pre - m:i + post + m].astype(np.float32)[None, :]
                Z = cb.zscore(W)
                T = np.median(np.stack(self._tpl), 0)[None, :] if self._tpl else Z
                self._tpl.append(Z[0])
                xc = cb._model_norm(W[:, m:m + L])
                tc = cb._model_norm(T[:, m:m + L])
                rows.append(len(beats_in))
                beats_in.append(np.stack([xc[0], xc[0] - tc[0]]))
                rr_in.append(feat)
            else:
                rows.append(None)            # edge of segment / rules-only: detected but not classified
            self._r_hist.append(rk)
            self._last_r = rk

        P = None
        if beats_in:
            P = self.clf.predict_inputs(np.stack(beats_in).astype(np.float32), np.stack(rr_in).astype(np.float32))
            best = (np.log(P + 1e-9) + self.bias).argmax(1)
        end = self.end_sample
        events = []
        for (rk, _), row in zip(ready, rows):
            sample = int(round(self._to_device(rk)))
            if row is None:
                label, conf, probs = "Unknown", 0.0, {c: 0.0 for c in self.classes}
            else:
                p, k = P[row], int(best[row])
                conf = float(p[k])
                label = self.classes[k] if conf >= self.thr else "Unknown"
                probs = {c: float(v) for c, v in zip(self.classes, p)}
            events.append(BeatEvent(sample=sample, time_s=sample / self.fs, label=label, confidence=conf,
                                    probs=probs, model_version=self.model_version, seg=self.seg_id,
                                    latency_s=(end - sample) / self.fs))
        return events
