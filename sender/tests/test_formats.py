"""File format detection/parsing (all formats) and annotation parsing."""
import time

import numpy as np
import pytest
from conftest import REC208

from stream_sender import FileSource, InputError, find_annotations, load_annotations


def read_all(src, start=0):
    src.open(start)
    out = []
    while chunk := src.read(1000):
        out += chunk
    src.close()
    return out


@pytest.mark.skipif(not REC208.exists(), reason="data/records/208.csv not present")
def test_mitbih_real_record_is_detected_and_lazy():
    t = time.perf_counter()
    src = FileSource(REC208)
    src.open(0)
    first = src.read(36)
    assert time.perf_counter() - t < 0.5            # first frame without parsing the 10 MB file
    assert (src.fmt, src.fs, src.lead, src.record) == ("mitbih", 360.0, "MLII", "208")
    assert src.adc == {"zero": 1024, "gain": 200.0, "units": "adu"}
    assert first[:4] == [1003.0] * 4 and src.pos == 36
    assert FileSource(REC208, lead="V1").col == 2
    src.open(649_990)                               # --start: rows are skipped, numbering stays absolute
    assert len(src.read(100)) == 10 and src.pos == 650_000
    idx, sym = load_annotations(find_annotations(REC208))
    assert (idx[0], sym[0], idx[1], sym[1]) == (46, "F", 209, "V")      # '+' rhythm marker at 9 is not a beat
    idx_all, sym_all = load_annotations(find_annotations(REC208), beats_only=False)
    assert (idx_all[0], sym_all[0]) == (9, "+") and len(idx_all) > len(idx)
    assert list(idx) == sorted(idx)


def test_mitbih_synthetic(mitbih_file):
    path, mlii, beats = mitbih_file
    src = FileSource(path)
    assert src.fmt == "mitbih" and read_all(src) == mlii.tolist()
    assert read_all(FileSource(path, lead="v1"))[:8] == [1000, 1001, 1002, 1003, 1004, 1005, 1006, 1000]
    assert read_all(src, start=3590) == mlii[3590:].tolist()
    with pytest.raises(InputError, match="not found"):
        FileSource(path, lead="V5")
    idx, sym = load_annotations(find_annotations(path))
    assert list(zip(idx.tolist(), sym)) == beats


def test_mitbih_without_mlii_falls_back_to_first_lead(tmp_path):
    p = tmp_path / "102.csv"
    p.write_text("'sample #','V5','V2'\n0,990,1001\n1,991,1002\n")
    src = FileSource(p)
    assert src.fmt == "mitbih" and src.lead == "V5" and read_all(src) == [990, 991] and src.notes


def test_legacy_hr_hrv_ecg_takes_last_value(tmp_path):
    p = tmp_path / "patient.txt"
    p.write_text("72,35,512\n72,35,530\ngarbage line\n73,36,498\n\n73,36,505\n")
    with pytest.raises(InputError, match="--fs is required"):
        FileSource(p)
    src = FileSource(p, fs=250)
    assert (src.fmt, src.fs) == ("legacy", 250.0)
    assert src.adc == {"zero": 512, "gain": 225.0, "units": "adu"}      # 10-bit kit defaults
    assert read_all(src) == [512, 530, 498, 505] and src.bad_lines == 1


def test_single_column_integers_and_millivolts(tmp_path):
    p = tmp_path / "adc.txt"
    p.write_text("\n".join(str(500 + i) for i in range(50)) + "\n")
    src = FileSource(p, fs=250)
    assert src.fmt == "single" and src.adc["zero"] == 512 and read_all(src) == [500 + i for i in range(50)]
    assert FileSource(p, fs=250, adc_zero=2048, adc_gain=400).adc == {"zero": 2048, "gain": 400, "units": "adu"}
    q = tmp_path / "mv.txt"
    q.write_text("0.012\n-0.034\n1.250\n")
    src = FileSource(q, fs=125)
    assert src.fmt == "single" and src.adc is None and read_all(src) == [0.012, -0.034, 1.25]
    assert FileSource(p, fs=250, mv=True).adc is None


@pytest.mark.parametrize("sep", [",", ";", "\t", " "])
def test_generic_csv_column_by_name_and_index(tmp_path, sep):
    p = tmp_path / "generic.csv"
    rows = [("time", "ecg", "resp")] + [(f"{i / 250:.3f}", str(510 + i), "7") for i in range(20)]
    p.write_text("\n".join(sep.join(r) for r in rows) + "\n")
    want = [510 + i for i in range(20)]
    by_name = FileSource(p, fs=250, column="ECG")
    assert by_name.fmt == "csv" and read_all(by_name) == want
    assert read_all(FileSource(p, fs=250, column="1")) == want
    assert read_all(FileSource(p, fs=250)) == [7.0] * 20            # default: last column (with a note)
    with pytest.raises(InputError, match="not found"):
        FileSource(p, fs=250, column="nope")


def test_headerless_multicolumn_with_column_index(tmp_path):
    p = tmp_path / "two.csv"
    p.write_text("0,601\n1,602\n2,603\n")
    assert read_all(FileSource(p, fs=200, column="1")) == [601, 602, 603]
    assert FileSource(p, fs=200).fmt == "csv"
    leg = tmp_path / "three.csv"
    leg.write_text("1,2,3\n4,5,6\n")
    assert read_all(FileSource(leg, fs=250, column="0")) == [1, 4]  # --column overrides the legacy rule


def test_missing_and_empty_files(tmp_path):
    with pytest.raises(InputError, match="no such file"):
        FileSource(tmp_path / "nope.csv")
    (tmp_path / "empty.csv").write_text("\n")
    with pytest.raises(InputError, match="empty"):
        FileSource(tmp_path / "empty.csv")
