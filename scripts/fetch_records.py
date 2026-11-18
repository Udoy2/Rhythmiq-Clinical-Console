#!/usr/bin/env python3
"""Fetch MIT-BIH Arrhythmia Database records from PhysioNet and write them in the team's text format.

    ~/.venvs/cardiosense/bin/python scripts/fetch_records.py 201 210 232        # -> data/records/
    ~/.venvs/cardiosense/bin/python scripts/fetch_records.py --all

For each record <id> this writes exactly what the team's original exports contain:
  <id>.csv              header `'sample #','MLII','V1'` (the record's own lead names), one row per sample,
                        raw ADC units (360 Hz, 11-bit, zero 1024, 200 adu/mV)
  <id>annotations.txt   `Time  Sample #  Type  Sub Chan Num [tab Aux]`, one row per annotation
Existing files are never overwritten (use --out DIR to write elsewhere, --verify to compare with existing ones).
Needs `wfdb` (in the project venv) and network access to physionet.org.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import wfdb

REPO = Path(__file__).resolve().parents[1]
ALL = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 111, 112, 113, 114, 115, 116, 117, 118, 119, 121, 122,
       123, 124, 200, 201, 202, 203, 205, 207, 208, 209, 210, 212, 213, 214, 215, 217, 219, 220, 221, 222, 223,
       228, 230, 231, 232, 233, 234]


def record_text(rec_id: str, pn_dir: str) -> tuple[str, str]:
    rec = wfdb.rdrecord(rec_id, pn_dir=pn_dir, physical=False)       # raw ADC units, no mV conversion
    ann = wfdb.rdann(rec_id, "atr", pn_dir=pn_dir)
    sig = np.column_stack([np.arange(rec.sig_len), rec.d_signal])
    header = ",".join(f"'{n}'" for n in ["sample #", *rec.sig_name])
    csv = header + "\n" + "\n".join(",".join(map(str, row)) for row in sig.tolist()) + "\n"

    lines = ["      Time   Sample #  Type  Sub Chan  Num\tAux"]
    aux = ann.aux_note or [""] * ann.ann_len
    for s, sym, sub, chan, num, note in zip(ann.sample, ann.symbol, ann.subtype, ann.chan, ann.num, aux):
        ms = (int(s) * 1000 * 2 + rec.fs) // (2 * rec.fs)            # round half up, integer arithmetic
        m, ms = divmod(int(ms), 60_000)
        h, m = divmod(m, 60)
        t = (f"{h}:{m:02d}" if h else f"{m}") + f":{ms / 1000:06.3f}"
        note = note.rstrip("\x00").strip()
        lines.append(f"{t:>12}{int(s):>9}{sym:>6}{int(sub):>5}{int(chan):>5}{int(num):>5}" + (f"\t{note}" if note else ""))
    return csv, "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("records", nargs="*", help="record ids, e.g. 201 210")
    ap.add_argument("--all", action="store_true", help="all 48 MIT-BIH records")
    ap.add_argument("--out", default=str(REPO / "data" / "records"), help="output directory")
    ap.add_argument("--db", default="mitdb", help="PhysioNet database directory")
    ap.add_argument("--verify", action="store_true", help="do not write; compare generated text with existing files in --out")
    args = ap.parse_args()
    ids = [str(r) for r in (ALL if args.all else args.records)]
    if not ids:
        ap.error("give record ids or --all")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    failed = 0
    for rid in ids:
        targets = (out / f"{rid}.csv", out / f"{rid}annotations.txt")
        if not args.verify and all(t.exists() for t in targets):
            print(f"{rid}: exists, skipped")
            continue
        try:
            texts = record_text(rid, args.db)
        except Exception as e:
            print(f"{rid}: download failed ({e})", file=sys.stderr)
            failed += 1
            continue
        for path, text in zip(targets, texts):
            if args.verify:
                same = path.exists() and path.read_text() == text
                print(f"{path.name}: {'identical' if same else 'DIFFERENT or missing'}")
                failed += not same
            elif path.exists():
                print(f"{path.name}: exists, not overwritten")
            else:
                path.write_text(text)
                print(f"{path.name}: written ({len(text) // 1024} KiB)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
