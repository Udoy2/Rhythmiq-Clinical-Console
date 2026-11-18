"""Shared fixtures/helpers. Run: ~/.venvs/cardiosense/bin/python -m pytest sender/tests -q"""
import asyncio
import socket
import sys
from pathlib import Path

import numpy as np
import pytest

SENDER = Path(__file__).resolve().parents[1]
REPO = SENDER.parent
sys.path.insert(0, str(SENDER))

import stream_sender  # noqa: E402
from cs_stream import Status  # noqa: E402
from mock_server import MockServer  # noqa: E402

REC208 = REPO / "data" / "records" / "208.csv"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_cfg(file, url, *extra, device="dev-test-1", key="k"):
    return stream_sender.build_parser().parse_args(
        ["--file", str(file), "--device", device, "--key", key, "--url", url, "--quiet", *map(str, extra)])


async def stream(file, *extra, server_kw=None, **kw):
    """Run one Streamer against a fresh MockServer; returns (exit code, streamer, server)."""
    async with MockServer(keep_samples=True, **(server_kw or {})) as srv:
        st = stream_sender.Streamer(make_cfg(file, srv.url, *extra, **kw), Status(quiet=True))
        code = await st.run()
        await asyncio.sleep(0.05)   # let the server finish handling bye/close
        return code, st, srv


@pytest.fixture
def mitbih_file(tmp_path):
    """Small synthetic record in the team's MIT-BIH text export format: 3600 samples, a beat every 300."""
    n = 3600
    mlii = (1024 + 100 * np.sin(np.arange(n) / 7.0)).astype(int)
    mlii[150::300] += 400
    p = tmp_path / "900.csv"
    p.write_text("'sample #','MLII','V1'\n" + "".join(f"{i},{mlii[i]},{1000 + i % 7}\n" for i in range(n)))
    syms = "NVNANFN+N~NV"
    rows = ["      Time   Sample #  Type  Sub Chan  Num\tAux"]
    for j, s in enumerate(syms):
        i = 150 + 300 * j
        rows.append(f"{f'0:{i / 360:06.3f}':>12}{i:>9}{s:>6}{0:>5}{0:>5}{0:>5}" + ("\t(N" if s == "+" else ""))
    (tmp_path / "900annotations.txt").write_text("\n".join(rows) + "\n")
    beats = [(150 + 300 * j, s) for j, s in enumerate(syms) if s not in "+~"]
    return p, mlii, beats
