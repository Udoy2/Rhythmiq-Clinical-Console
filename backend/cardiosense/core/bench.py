"""Replay a recording through `StreamProcessor` and report accuracy, latency and CPU numbers.

    python -m cardiosense.core.bench ../data/records/208.csv [--seconds 300] [--frame-ms 100]

CSV: `'sample #','MLII','V1'` (first signal column is used); ground truth is read from
`<id>annotations.txt` next to it when present. The helpers are reused by the tests.
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .model import default_model_dir, load_classifier
from .stream import StreamProcessor
from .types import AAMI, LABELS, MIT_BEAT_SYMBOLS


def load_record(csv_path, max_seconds: float | None = None, fs: int = 360):
    """-> (raw samples of the first lead, annotations [{"sample","symbol"}])."""
    csv_path = Path(csv_path)
    rows = None if max_seconds is None else int(max_seconds * fs)
    x = np.loadtxt(csv_path, delimiter=",", skiprows=1, usecols=1, max_rows=rows, dtype=np.float64)
    ann = []
    ann_path = csv_path.with_name(csv_path.stem + "annotations.txt")
    if ann_path.is_file():
        with open(ann_path) as fh:
            next(fh, None)
            for line in fh:
                p = line.split()
                if len(p) >= 3 and p[1].isdigit() and int(p[1]) < len(x):
                    ann.append({"sample": int(p[1]), "symbol": p[2]})
    return x, ann


@dataclass
class Replay:
    fs: int
    n_samples: int
    beats: list = field(default_factory=list)
    windows: list = field(default_factory=list)
    alerts: list = field(default_factory=list)
    quality: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    tasks: list = field(default_factory=list)
    push_ms: list = field(default_factory=list)
    wall_s: float = 0.0
    signal_samples: int = 0

    def collect(self, out) -> None:
        self.beats += out.beats; self.windows += out.windows; self.alerts += out.alerts
        self.quality += out.quality; self.gaps += out.gaps; self.tasks += out.tasks
        if out.signal:
            self.signal_samples += len(out.signal["mv"])

    @property
    def realtime_factor(self) -> float:
        return (self.n_samples / self.fs) / self.wall_s if self.wall_s else float("inf")

    def cpu(self) -> dict:
        a = np.asarray(self.push_ms)
        return {"mean": float(a.mean()), "p50": float(np.percentile(a, 50)), "p95": float(np.percentile(a, 95)),
                "max": float(a.max())}


def replay(sp: StreamProcessor, samples, annotations=(), frame_ms: float = 100.0) -> Replay:
    """Stream `samples` in frames (like the sender does) and collect everything the processor emits."""
    fs = sp.fs
    n = max(1, int(round(fs * frame_ms / 1000.0)))
    ann = sorted(annotations, key=lambda a: a["sample"])
    res, ai = Replay(fs, len(samples)), 0
    start = time.perf_counter()
    for t0 in range(0, len(samples), n):
        frame = samples[t0:t0 + n]
        aj = ai
        while aj < len(ann) and ann[aj]["sample"] < t0 + len(frame):
            aj += 1
        tic = time.perf_counter()
        out = sp.push(frame, t0, False, ann[ai:aj] or None)
        res.push_ms.append((time.perf_counter() - tic) * 1000.0)
        res.collect(out)
        ai = aj
    res.collect(sp.flush())
    res.wall_s = time.perf_counter() - start
    return res


def r_peak_scores(beats, annotations, fs: int, tol_s: float = 0.150) -> dict:
    """Sensitivity / positive predictivity of the detected R-peaks against the beat annotations (one-to-one)."""
    ref = np.array(sorted(a["sample"] for a in annotations if a["symbol"] in MIT_BEAT_SYMBOLS))
    det = np.array([b["sample"] for b in beats])
    tol, used, tp = tol_s * fs, np.zeros(len(det), bool), 0
    for s in ref:
        j = np.searchsorted(det, s)
        best = None
        for c in (j - 1, j):
            if 0 <= c < len(det) and not used[c] and abs(det[c] - s) <= tol and \
                    (best is None or abs(det[c] - s) < abs(det[best] - s)):
                best = c
        if best is not None:
            used[best] = True
            tp += 1
    return {"reference": len(ref), "detected": len(det), "tp": tp,
            "sensitivity": tp / len(ref) if len(ref) else float("nan"),
            "ppv": tp / len(det) if len(det) else float("nan")}


def offline_agreement(clf, mv, fs: int, beats, tol_samples: int = 3) -> dict:
    """Streaming labels vs `BeatClassifier.classify` on the whole signal (matched by R-peak position)."""
    offline = clf.classify(mv, fs)
    F = clf.cfg["fs"]
    pos = np.array([b["sample"] for b in beats])
    matched = same = 0
    diffs = []
    for o in offline:
        s = o["sample"] * fs / F
        j = np.searchsorted(pos, s)
        c = min((c for c in (j - 1, j) if 0 <= c < len(pos)), key=lambda c: abs(pos[c] - s), default=None)
        if c is None or abs(pos[c] - s) > tol_samples:
            continue
        matched += 1
        if beats[c]["label"] == o["label"]:
            same += 1
        else:
            diffs.append((beats[c]["time_s"], o["label"], beats[c]["label"]))
    return {"offline": len(offline), "streaming": len(beats), "matched": matched,
            "coverage": matched / len(offline) if offline else float("nan"),
            "agreement": same / matched if matched else float("nan"), "differences": diffs}


def confusion(beats) -> dict:
    """truth (AAMI) -> predicted label counts, over beats that carry ground truth."""
    m = {t: {p: 0 for p in LABELS} for t in ("N", "S", "V", "F")}
    for b in beats:
        if b.get("truth") in m:
            m[b["truth"]][b["label"]] += 1
    return m


def format_confusion(m: dict) -> str:
    lines = ["truth\\pred " + "".join(f"{p:>9}" for p in LABELS) + "   recall"]
    for t, row in m.items():
        n = sum(row.values())
        lines.append(f"{t:<11}" + "".join(f"{row[p]:>9}" for p in LABELS) +
                     (f"   {row[t] / n:6.3f}" if n else "        -"))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("csv")
    ap.add_argument("--fs", type=int, default=360)
    ap.add_argument("--zero", type=float, default=1024.0)
    ap.add_argument("--gain", type=float, default=200.0)
    ap.add_argument("--seconds", type=float, default=None, help="only the first N seconds")
    ap.add_argument("--frame-ms", type=float, default=100.0)
    ap.add_argument("--model-dir", default=None)
    ap.add_argument("--rules-only", action="store_true")
    a = ap.parse_args(argv)

    x, ann = load_record(a.csv, a.seconds, a.fs)
    clf = None if a.rules_only else load_classifier(a.model_dir or default_model_dir())
    sp = StreamProcessor(a.fs, clf, a.zero, a.gain)
    res = replay(sp, x, ann, a.frame_ms)
    st = sp.stats()
    dur = len(x) / a.fs
    print(f"record {Path(a.csv).stem}: {dur:.0f} s at {a.fs} Hz, {len(res.push_ms)} frames of {a.frame_ms:.0f} ms")
    print(f"model: {sp.model_version or 'none (rules-only mode)'}   rules: {sp.rule_version}")
    print(f"beats: {st['beats']}  {st['labels']}")
    if ann:
        rp = r_peak_scores(res.beats, ann, a.fs)
        print(f"R-peaks vs annotations: sensitivity {rp['sensitivity']:.4f}  +P {rp['ppv']:.4f} "
              f"({rp['tp']}/{rp['reference']} annotated beats, {rp['detected']} detected)")
    if clf is not None:
        ag = offline_agreement(clf, (x - a.zero) / a.gain, a.fs, res.beats)
        print(f"streaming vs offline labels: agreement {ag['agreement']:.4f} on {ag['matched']} matched beats "
              f"(offline {ag['offline']}, coverage {ag['coverage']:.4f})")
    lat = st["latency_s"]
    print(f"beat latency (signal time R-peak -> emission): median {lat['median']:.2f} s  p95 {lat['p95']:.2f} s  "
          f"max {lat['max']:.2f} s")
    cpu = res.cpu()
    print(f"CPU per push: mean {cpu['mean']:.2f} ms  p50 {cpu['p50']:.2f} ms  p95 {cpu['p95']:.2f} ms  "
          f"max {cpu['max']:.2f} ms   (frame period {a.frame_ms:.0f} ms)")
    print(f"throughput: {dur:.0f} s of signal in {res.wall_s:.1f} s = {res.realtime_factor:.0f}x real time")
    if ann:
        print("\nconfusion matrix (AAMI ground truth vs streaming label):")
        print(format_confusion(confusion(res.beats)))
        scored = sum(1 for s in ann if AAMI.get(s["symbol"]))
        print(f"({scored} scored annotations; paced / unclassifiable symbols carry no truth)")
    tiers = {}
    for w in res.windows:
        tiers[w["risk"]["tier"]] = tiers.get(w["risk"]["tier"], 0) + 1
    sqi = [w["sqi"] for w in res.windows]
    print(f"\nwindows: {len(res.windows)}  risk tiers {tiers}  SQI median {np.median(sqi):.2f} min {min(sqi):.2f}"
          if sqi else "\nwindows: 0")
    print(f"quality changes: {[(q['state'], q['sqi']) for q in res.quality]}")
    print(f"gaps: {res.gaps}   device tasks: {[t['rule_id'] for t in res.tasks]}")
    print(f"\nalerts raised ({len(res.alerts)}):")
    for al in res.alerts:
        ev = al["evidence"]
        print(f"  {ev['finding']['end_s']:8.1f} s  {al['tier']:<6} {al['rule_id']:<15} [{ev['reason']}] {al['detail']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
