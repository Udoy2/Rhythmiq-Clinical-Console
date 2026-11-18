#!/usr/bin/env python
"""Check the exported artefact through the real inference path (notebook cell 28).

    python ml/verify_model.py                      # records 100, 119, 208 + data/records/208.csv
    python ml/verify_model.py --records 100 233
    python ml/verify_model.py --ds2                # end-to-end (own R-peak detector) over all DS2 records

Uses only `cardiosense_beats.BeatClassifier(ts_path, cfg_path).classify(raw, fs_in)`.
"Agreement" = share of detected beats matched (+-150 ms) to a cardiologist N/S/V/F annotation whose
label equals the annotation's AAMI class ("Unknown" counts as a disagreement).
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from cardiosense_beats import CLASSES, BeatClassifier  # noqa: E402

AAMI = {**dict.fromkeys("NLRej", "N"), **dict.fromkeys("AaJS", "S"),
        **dict.fromkeys("VE", "V"), "F": "F", **dict.fromkeys("/fQ", "Q")}
DS2 = ["100", "103", "105", "111", "113", "117", "121", "123", "200", "202", "210",
       "212", "213", "214", "219", "221", "222", "228", "231", "232", "233", "234"]
DS1 = ["101", "106", "108", "109", "112", "114", "115", "116", "118", "119", "122",
       "124", "201", "203", "205", "207", "208", "209", "215", "220", "223", "230"]


def match_peaks(det, ref, tol):
    j = np.searchsorted(ref, det)
    used = np.zeros(len(ref), bool)
    pairs = []
    for i, d in enumerate(det):
        best, bd = -1, tol + 1
        for k in (j[i] - 1, j[i]):
            if 0 <= k < len(ref) and not used[k] and abs(ref[k] - d) <= tol and abs(ref[k] - d) < bd:
                best, bd = k, abs(ref[k] - d)
        if best >= 0:
            used[best] = True
            pairs.append((i, best))
    return np.array(pairs, dtype=int).reshape(-1, 2)


def score(res, ann_sample, ann_symbol, fs_in, fs, dt, dur_s):
    sym = np.asarray(ann_symbol)
    beat = np.isin(sym, list(AAMI))
    ref = np.round(np.asarray(ann_sample)[beat] * fs / fs_in).astype(int)
    ref_lab = np.array([AAMI[c] for c in sym[beat]])
    det = np.array([b["sample"] for b in res])
    pairs = match_peaks(det, ref, int(0.15 * fs))
    yt = [(ref_lab[j], res[i]["label"]) for i, j in pairs if ref_lab[j] in CLASSES]
    out = {"beats": len(res), "ref_beats": int(len(ref)), "matched": int(len(pairs)),
           "det_Se": len(pairs) / max(1, len(ref)), "det_+P": len(pairs) / max(1, len(det)),
           "agreement": float(np.mean([t == p for t, p in yt])),
           "unknown": float(np.mean([b["label"] == "Unknown" for b in res])),
           "cpu_s": dt, "realtime_factor": dur_s / dt}
    hit = np.zeros(len(ref), bool)
    hit[pairs[:, 1]] = True
    out["missed_by_detector"] = {c: int(((ref_lab == c) & ~hit).sum()) for c in CLASSES if ((ref_lab == c) & ~hit).any()}
    for c in CLASSES:
        n = sum(t == c for t, _ in yt)
        if n:
            out[f"{c}_n"] = n
            out[f"{c}_Se"] = sum(t == c and p == c for t, p in yt) / n
    return out, yt


def fmt(name, o):
    per = " ".join(f"{c}:{o[f'{c}_Se']:.2f}(n={o[f'{c}_n']})" for c in CLASSES if f"{c}_n" in o)
    return (f"{name}: {o['beats']} beats | det Se {o['det_Se']:.3f} +P {o['det_+P']:.3f} | agreement "
            f"{o['agreement']:.3f} | unknown {o['unknown']:.3f} | Se {per} | {o['cpu_s']:.2f}s CPU, "
            f"realtime x{o['realtime_factor']:.0f} | missed by detector {o['missed_by_detector']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=os.environ.get("CARDIOSENSE_MODEL_DIR",
                                                          str(HERE / "models" / "cardiosense_beat_model")))
    ap.add_argument("--mitdb", default=str(ROOT / "data" / "physionet" / "mitdb"))
    ap.add_argument("--records", nargs="+", default=["100", "119", "208"])
    ap.add_argument("--csv", default=str(ROOT / "data" / "records" / "208.csv"))
    ap.add_argument("--ds2", action="store_true", help="end-to-end macro metrics over all DS2 records")
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--json", help="write results to this file")
    a = ap.parse_args()

    import torch
    import wfdb
    torch.set_num_threads(a.threads)
    md = Path(a.model_dir)
    clf = BeatClassifier(str(md / "beat_model_ts.pt"), str(md / "model_config.json"))
    fs = clf.cfg["fs"]
    print("model:", clf.cfg["model_version"], "| stand_in:", clf.cfg.get("stand_in", False), "| threads:", a.threads)
    results, by_rec = {}, {}

    def run(rec):
        s, an = wfdb.rdrecord(f"{a.mitdb}/{rec}"), wfdb.rdann(f"{a.mitdb}/{rec}", "atr")
        raw = s.p_signal[:, s.sig_name.index("MLII")]
        clf.classify(raw[:3600], s.fs)                      # warm-up (JIT)
        t0 = time.perf_counter()
        res = clf.classify(raw, s.fs)
        dt = time.perf_counter() - t0
        o, yt = score(res, an.sample, an.symbol, s.fs, fs, dt, len(raw) / s.fs)
        o["split"] = "DS1" if rec in DS1 else "DS2" if rec in DS2 else "other"
        return res, an, o, yt

    for rec in a.records:
        res, an, o, _ = run(rec)
        results[rec], by_rec[rec] = o, (res, an)
        print(fmt(f"record {rec} [{o['split']}]", o))

    if a.csv and os.path.exists(a.csv):
        rec = Path(a.csv).stem
        adc = np.loadtxt(a.csv, delimiter=",", skiprows=1, usecols=1)
        raw = (adc - 1024.0) / 200.0                        # 11-bit ADC units -> mV
        t0 = time.perf_counter()
        res = clf.classify(raw, 360)
        dt = time.perf_counter() - t0
        if rec not in by_rec:
            by_rec[rec] = run(rec)[:2]
        wres, an = by_rec[rec]
        o, _ = score(res, an.sample, an.symbol, 360, fs, dt, len(raw) / 360)
        same_pos = len(res) == len(wres) and all(x["sample"] == y["sample"] for x, y in zip(res, wres))
        same_lab = same_pos and float(np.mean([x["label"] == y["label"] for x, y in zip(res, wres)]))
        o["same_beats_as_wfdb"], o["label_match_vs_wfdb"] = bool(same_pos), same_lab
        results[f"{rec}.csv"] = o
        print(fmt(f"text export {Path(a.csv).name}", o))
        print(f"   vs wfdb path: same beat positions {same_pos}, identical labels {same_lab}")

    if a.ds2:
        Y, TP, NR, ND, cpu, dur = [], 0, 0, 0, 0.0, 0.0
        for rec in DS2:
            _, _, o, yt = run(rec)
            Y += yt; TP += o["matched"]; NR += o["ref_beats"]; ND += o["beats"]
            cpu += o["cpu_s"]; dur += o["cpu_s"] * o["realtime_factor"]
        cm = np.zeros((4, 5), int)
        for t, p in Y:
            cm[CLASSES.index(t), (CLASSES + ["Unknown"]).index(p)] += 1
        tp = np.diag(cm[:, :4])
        se, ppv = tp / np.maximum(cm.sum(1), 1), tp / np.maximum(cm[:, :4].sum(0), 1)
        f1 = 2 * se * ppv / np.maximum(se + ppv, 1e-9)
        e2e = {"condition": "DS2, own R-peak detector, 360 Hz input, labels incl. Unknown threshold "
                            "(an Unknown beat counts as a miss)",
               "det_Se": TP / NR, "det_+P": TP / ND, "acc": float(tp.sum() / cm.sum()), "macroF1": float(f1.mean()),
               **{f"{c}_Se": float(se[i]) for i, c in enumerate(CLASSES)},
               **{f"{c}_+P": float(ppv[i]) for i, c in enumerate(CLASSES)},
               "unknown_rate": float(cm[:, 4].sum() / cm.sum()), "realtime_factor": dur / cpu,
               "confusion_matrix_rows_true_cols_pred_NSVF_Unknown": cm.tolist()}
        results["ds2_end_to_end"] = e2e
        print("DS2 end-to-end:", json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in e2e.items()}))

    if a.json:
        Path(a.json).write_text(json.dumps(results, indent=2, default=float))


if __name__ == "__main__":
    main()
