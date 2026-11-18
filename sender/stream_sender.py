#!/usr/bin/env python3
"""CardioSense replay sender: stream a patient recording (text file) to the backend as live ECG frames.

    python sender/stream_sender.py --file data/records/208.csv --device dev-demo-208 --key demo-key-208 \
        --url ws://localhost:8000 --speed 1

Replaces the prototype's sender_serial.py (file -> serial port, one `HR,HRV,ECG` line per sample) with
file -> WebSocket frames per docs/CONTRACT.md §2.1. Deps: websockets, numpy. See sender/README.md.
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import re
import signal
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cs_stream import (EXIT_INPUT, EXIT_OK, EXIT_SIGINT, EXIT_SIGTERM, ConnectionLost, FatalStreamError,  # noqa: E402
                       Pacer, Status, StreamClient, fmt_time, frame_sizes, hello_msg, parse_span)

# MIT-BIH beat annotation symbols (wfdb "beat" class); everything else (+ ~ | " ! [ ] x ...) is a non-beat marker
BEAT_SYMBOLS = frozenset("NLRBAaJSVrFejnE/fQ?")
MITBIH = {"fs": 360.0, "zero": 1024, "gain": 200.0}      # 11-bit ADC, 200 adu/mV (MIT-BIH header values)
# AD8232 kit on a 10-bit, 5 V Arduino ADC: mid-rail 512; gain is not calibrated. Nominal value from the
# SparkFun board's total gain (~1100 V/V): 1 mV * 1100 / (5000 mV / 1023 adu) ~= 225 adu/mV.
KIT = {"zero": 512, "gain": 225.0}


class InputError(Exception):
    pass


def _is_num(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def _fields(line: str, sep) -> list[str]:
    return [p.strip().strip("'\"") for p in (line.split(sep) if sep else line.split())]


class FileSource:
    """Lazy reader for one recording. Formats (auto-detected from the first lines):

    mitbih  header `'sample #','MLII','V1'`, one row per sample -> column picked by --lead (360 Hz, zero 1024, gain 200)
    legacy  headerless `HR,HRV,ECG` lines (prototype serial log) -> last value (10-bit kit ADC)
    single  one number per line
    csv     any other delimited text, with or without a header -> --column (name or 0-based index; default last)
    """

    def __init__(self, path, fs=None, lead="MLII", column=None, adc_zero=None, adc_gain=None, mv=False):
        self.path = Path(path)
        if not self.path.is_file():
            raise InputError(f"no such file: {self.path}")
        with open(self.path, "r", errors="replace") as f:
            head = [ln for ln in itertools.islice(f, 200) if ln.strip()][:64]
        if not head:
            raise InputError(f"{self.path} is empty")
        self.sep = next((s for s in (",", ";", "\t") if s in head[0]), None)
        first = _fields(head[0], self.sep)
        self.header = first if not all(_is_num(p) for p in first) else None
        self.notes: list[str] = []
        names = [n.lower() for n in self.header] if self.header else []
        ncol = len(first)

        if self.header and len(names) >= 2 and names[0].startswith("sample"):
            self.fmt = "mitbih"
            want = column if column is not None else lead
            self.col = self._lookup(want, names)
            if self.col is None and column is None and lead.upper() == "MLII":
                self.col = 1
                self.notes.append(f"no MLII lead in {self.path.name}; using first signal column '{self.header[1]}'")
        elif not self.header and ncol == 1:
            self.fmt, self.col = "single", 0
        elif not self.header and ncol == 3 and column is None:
            self.fmt, self.col = "legacy", 2
        else:
            self.fmt = "csv"
            self.col = self._lookup(column, names) if column is not None else ncol - 1
            if column is None:
                self.notes.append(f"--column not given; using last column ({self.header[-1] if self.header else ncol - 1})")
        if self.col is None or not 0 <= self.col < ncol:
            raise InputError(f"column/lead {column if column is not None else lead!r} not found in {self.path.name}; "
                             f"available: {self.header or list(range(ncol))}")
        self.lead = self.header[self.col] if self.header else str(self.col)

        self.fs = float(fs) if fs else (MITBIH["fs"] if self.fmt == "mitbih" else None)
        if not self.fs:
            raise InputError(f"--fs is required for '{self.fmt}' files (60-250 Hz; the prototype kit logged 250 lines/s)")
        if self.fs <= 0:
            raise InputError("--fs must be positive")
        if self.fmt != "mitbih" and not 60 <= self.fs <= 250:
            self.notes.append(f"fs={self.fs:g} Hz is outside the supported device range 60-250 Hz")

        # units: ADC counts with (zero, gain) or already mV (adc=None -> `adc` omitted from hello)
        body = head[1:] if self.header else head
        vals = [v for v in (self._value(ln) for ln in body) if v is not None]
        if not vals:
            raise InputError(f"no numeric samples found in column {self.col} of {self.path.name}")
        integral = all(float(v).is_integer() for v in vals)
        base = MITBIH if self.fmt == "mitbih" else KIT if (self.fmt == "legacy" or integral) else None
        if mv or (base is None and adc_zero is None and adc_gain is None):
            self.adc = None
        else:
            base = base or KIT
            self.adc = {"zero": base["zero"] if adc_zero is None else adc_zero,
                        "gain": base["gain"] if adc_gain is None else adc_gain, "units": "adu"}
            if self.adc["gain"] <= 0:
                raise InputError("--adc-gain must be > 0")
        self.record = self.path.stem
        self.bad_lines = 0
        self._f = None
        self.pos = 0  # file sample index of the next sample `read` returns

    @staticmethod
    def _lookup(want, names):
        want = str(want).strip().strip("'\"")
        if want.lower() in names:
            return names.index(want.lower())
        return int(want) if re.fullmatch(r"\d+", want) else None

    def _value(self, line: str):
        try:
            return float((line.split(self.sep) if self.sep else line.split())[self.col].strip().strip("'\""))
        except (ValueError, IndexError):
            return None

    def open(self, start: int = 0):
        """(Re)open positioned at file sample index `start` (rows are skipped, not parsed)."""
        self.close()
        self._f = open(self.path, "r", errors="replace")
        if self.header:
            self._f.readline()
        for _ in itertools.islice(self._f, start):
            pass
        self.pos = start

    def read(self, n: int) -> list[float]:
        """Next up-to-n samples; shorter (or empty) only at end of file. Unparseable lines are skipped."""
        out: list[float] = []
        while len(out) < n:
            lines = list(itertools.islice(self._f, n - len(out)))
            if not lines:
                break
            for ln in lines:
                v = self._value(ln)
                if v is None:
                    self.bad_lines += bool(ln.strip())
                else:
                    out.append(v)
        self.pos += len(out)
        return out

    def close(self):
        if self._f:
            self._f.close()
            self._f = None


def find_annotations(path: Path) -> Path | None:
    """Sibling ground-truth file: `<id>annotations.txt` (team export naming), a few variants tolerated."""
    for name in (f"{path.stem}annotations.txt", f"{path.stem}_annotations.txt", f"{path.stem}.annotations.txt"):
        if (p := path.with_name(name)).is_file():
            return p
    return None


def load_annotations(path, beats_only: bool = True) -> tuple[np.ndarray, list[str]]:
    """Parse `Time  Sample#  Type  Sub Chan Num [Aux]` rows -> (sorted sample indices, raw MIT symbols)."""
    rows = []
    with open(path, "r", errors="replace") as f:
        for ln in f:
            p = ln.split()
            if len(p) >= 3 and p[1].isdigit() and (not beats_only or p[2] in BEAT_SYMBOLS):
                rows.append((int(p[1]), p[2]))
    rows.sort(key=lambda r: r[0])
    return np.array([r[0] for r in rows], dtype=np.int64), [r[1] for r in rows]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Stream an ECG recording to CardioSense as live WebSocket frames.",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--file", required=True, help="recording: MIT-BIH CSV export, legacy HR,HRV,ECG lines, "
                                                  "single-column text or generic CSV (auto-detected)")
    ap.add_argument("--device", required=True, help="paired device id, e.g. dev-demo-208")
    ap.add_argument("--key", required=True, help="device key, e.g. demo-key-208")
    ap.add_argument("--url", default="ws://localhost:8000", help="backend base URL (connects to /v1/stream/{device}?key=)")
    ap.add_argument("--fs", type=float, default=None, help="sample rate in Hz (default 360 for MIT-BIH CSV; "
                                                           "required, 60-250, for other formats)")
    ap.add_argument("--lead", default="MLII", help="lead/column name for MIT-BIH CSV")
    ap.add_argument("--column", default=None, help="column name or 0-based index for generic CSV")
    ap.add_argument("--speed", type=float, default=1.0, help="1 = real time, 10 = 10x, 0 = as fast as possible")
    ap.add_argument("--start", type=float, default=0.0, help="start offset into the file, seconds")
    ap.add_argument("--duration", type=float, default=None, help="seconds of signal to send (default: to end of file)")
    ap.add_argument("--loop", action="store_true", help="restart from --start at end of file")
    ap.add_argument("--frame-ms", type=float, default=100.0, help="frame length in ms")
    ap.add_argument("--annotations", default=None, help="ground-truth file (default: sibling <id>annotations.txt)")
    ap.add_argument("--no-annotations", action="store_true", help="do not send ground-truth annotations")
    ap.add_argument("--all-annotations", action="store_true", help="also send non-beat markers (+ ~ | ...)")
    ap.add_argument("--adc-zero", type=float, default=None, help="ADC baseline (default per format: 1024 MIT-BIH, 512 kit)")
    ap.add_argument("--adc-gain", type=float, default=None, help="ADC units per mV (default per format: 200 MIT-BIH, 225 kit nominal)")
    ap.add_argument("--mv", action="store_true", help="samples are already mV (omit adc from hello)")
    ap.add_argument("--record", default=None, help="record label in hello (default: file stem)")
    ap.add_argument("--dataset", default=None, help="dataset label in hello (default: MIT-BIH for MIT-BIH CSV)")
    ap.add_argument("--lead-off-at", action="append", default=[], metavar="T:DUR",
                    help="fault: electrodes off from T for DUR s of stream time (lead_off=true, flat signal); repeatable")
    ap.add_argument("--dropout-at", action="append", default=[], metavar="T:DUR",
                    help="fault: drop DUR s of samples at T (t0 jumps -> server records a gap); repeatable")
    ap.add_argument("--noise-db", type=float, default=None, metavar="SNR",
                    help="fault: add white noise at this SNR in dB (lower = noisier; ~0 trips the SQI gate)")
    ap.add_argument("--noise-at", action="append", default=[], metavar="T:DUR",
                    help="restrict --noise-db to these spans (default: whole stream); repeatable")
    ap.add_argument("--max-retries", type=int, default=8, help="consecutive failed connects before giving up (0 = forever)")
    ap.add_argument("--seed", type=int, default=None, help="RNG seed for --noise-db")
    ap.add_argument("--quiet", action="store_true", help="errors only")
    return ap


class Streamer:
    """One file -> one device stream. `await run()` returns a process exit code."""

    def __init__(self, cfg: argparse.Namespace, status: Status | None = None):
        self.cfg = cfg
        self.status = status or Status(cfg.quiet)
        self.tag = f"[{cfg.device}]"
        self.src = src = FileSource(cfg.file, cfg.fs, cfg.lead, cfg.column, cfg.adc_zero, cfg.adc_gain, cfg.mv)
        self.fs = src.fs
        if cfg.frame_ms <= 0 or cfg.speed < 0 or cfg.start < 0 or (cfg.duration is not None and cfg.duration <= 0):
            raise InputError("--frame-ms and --duration must be > 0, --speed and --start >= 0")
        try:
            spans = {k: [tuple(round(t * self.fs) for t in parse_span(s)) for s in getattr(cfg, k)]
                     for k in ("lead_off_at", "dropout_at", "noise_at")}
        except ValueError as e:
            raise InputError(str(e)) from None
        self.lead_off_spans, self.dropout_spans, self.noise_spans = spans.values()

        self.ann_idx, self.ann_sym = np.array([], dtype=np.int64), []
        if not cfg.no_annotations:
            ann = Path(cfg.annotations) if cfg.annotations else find_annotations(src.path)
            if cfg.annotations and not ann.is_file():
                raise InputError(f"no such annotations file: {ann}")
            if ann:
                self.ann_idx, self.ann_sym = load_annotations(ann, beats_only=not cfg.all_annotations)
                src.notes.append(f"{len(self.ann_sym)} ground-truth annotations from {ann.name}")

        self.start_row = round(cfg.start * self.fs)
        self.limit = round(cfg.duration * self.fs) if cfg.duration else None
        self.integral = src.adc is not None
        self.flat = float(src.adc["zero"]) if src.adc else 0.0   # electrodes-off level (baseline)
        self.noise_sigma = 0.0
        if cfg.noise_db is not None:
            src.open(self.start_row)
            ref = np.asarray(src.read(round(10 * self.fs)))        # signal power from the first 10 s
            self.noise_sigma = float(ref.std()) * 10 ** (-cfg.noise_db / 20) if len(ref) else 0.0
            self.rng = np.random.default_rng(cfg.seed)
        dataset = cfg.dataset or ("MIT-BIH" if src.fmt == "mitbih" else None)
        hello = hello_msg(cfg.device, self.fs, "replay", src.adc, record=cfg.record or src.record,
                          dataset=dataset, speed=cfg.speed)
        self.client = StreamClient(cfg.url, cfg.device, cfg.key, hello, max_retries=cfg.max_retries,
                                   log=lambda m: self.status.event(f"{self.tag} {m}"))
        self.pacer = Pacer(self.fs, cfg.speed)
        self.pos = 0            # stream sample index (signal time = pos/fs), counts dropped samples too
        self.dropped = 0
        self.signal = None      # set by the signal handler
        self.exit_code = None

    # -- reading ---------------------------------------------------------------------------------------
    def _read_frame(self, n: int):
        """n samples (fewer at the very end) + [(offset_in_frame, symbol)], wrapping around when --loop."""
        samples: list[float] = []
        anns: list[tuple[int, str]] = []
        while len(samples) < n:
            a = self.src.pos
            chunk = self.src.read(n - len(samples))
            if chunk:
                lo, hi = np.searchsorted(self.ann_idx, [a, a + len(chunk)])
                anns += [(int(self.ann_idx[i]) - a + len(samples), self.ann_sym[i]) for i in range(lo, hi)]
                samples += chunk
            if len(samples) < n:
                if not (self.cfg.loop and (chunk or self.src.pos > self.start_row)):
                    break
                self.src.open(self.start_row)
        return samples, anns

    @staticmethod
    def _in(spans, a: int, b: int) -> bool:
        """Does frame [a, b) start inside one of the spans? (faults are frame-granular)"""
        return any(s <= a < e for s, e in spans)

    def _encode(self, x) -> list:
        return np.rint(x).astype(np.int64).tolist() if self.integral else np.round(x, 4).tolist()

    # -- main loop -------------------------------------------------------------------------------------
    async def run(self) -> int:
        cfg, src, c = self.cfg, self.src, self.client
        ev = lambda m: self.status.event(f"{self.tag} {m}")  # noqa: E731
        for note in src.notes:
            ev(note)
        units = f"adc zero={src.adc['zero']:g} gain={src.adc['gain']:g}/mV" if src.adc else "mV"
        ev(f"{src.path.name}: format={src.fmt} lead={src.lead} fs={self.fs:g} Hz {units} speed={cfg.speed:g}x "
           f"-> {c.url.split('?')[0]}")
        code, t_start = EXIT_OK, time.monotonic()
        try:
            src.open(self.start_row)
            await self._connect(first=True)
            for n in frame_sizes(self.fs, cfg.frame_ms):
                if self.limit is not None:
                    n = min(n, self.limit - self.pos)
                    if n <= 0:
                        break
                samples, anns = self._read_frame(n)
                if not samples:
                    break
                a, b = self.pos, self.pos + len(samples)
                await self.pacer.wait(b)            # a frame is due when its last sample "has been acquired"
                if self._in(self.dropout_spans, a, b):
                    c.skip(len(samples))
                    self.dropped += len(samples)
                else:
                    lead_off = self._in(self.lead_off_spans, a, b)
                    x = np.asarray(samples, dtype=float)
                    if lead_off:
                        x, anns = np.full(len(x), self.flat), []
                    elif self.noise_sigma and (not self.noise_spans or self._in(self.noise_spans, a, b)):
                        x = x + self.rng.normal(0.0, self.noise_sigma, len(x))
                    payload = self._encode(x)
                    while True:
                        try:
                            await c.send_frame(payload, lead_off, anns)
                            break
                        except ConnectionLost as e:
                            ev(f"connection lost at {fmt_time(a / self.fs)} ({e}); reconnecting")
                            await self._connect(first=False)
                            self.pacer.reset(b)     # resume from this frame; do not burst the outage
                self.pos = b
                self.status.update(lambda: self._line(t_start))
            await c.close(bye=True)
        except asyncio.CancelledError:
            code = EXIT_SIGTERM if self.signal == signal.SIGTERM else EXIT_SIGINT
            ev("interrupted; sending bye")
            try:
                await asyncio.wait_for(c.close(bye=True), 3)
            except Exception:
                pass
        except FatalStreamError as e:
            code = e.code
            print(f"{self.tag} error: {e}", file=sys.stderr, flush=True)
            await c.close(bye=False)
        finally:
            src.close()
        wall = time.monotonic() - t_start
        ev(f"done: {fmt_time(self.pos / self.fs)} of signal, {c.frames} frames, {c.samples} samples in {wall:.1f}s "
           f"({c.samples / wall if wall else 0:.0f} sps), reconnects {c.reconnects}"
           + (f", dropped {self.dropped} samples (injected)" if self.dropped else "")
           + (f", skipped {src.bad_lines} unparseable lines" if src.bad_lines else "") + f", exit {code}")
        self.exit_code = code
        return code

    async def _connect(self, first: bool):
        ack = await self.client.connect()
        self.pacer.reset(self.pos)
        self.status.event(f"{self.tag} {'connected' if first else 'reconnected (new hello, new session, t0 restarts at 0)'}: "
                          f"session {ack.get('session_id')} model_loaded={ack.get('model_loaded')}")

    def _line(self, t_start: float) -> str:
        wall, c = time.monotonic() - t_start, self.client
        sps = c.samples / wall if wall > 0 else 0.0
        return (f"{self.tag} {fmt_time(self.pos / self.fs)} signal | {c.frames} frames | {sps:.1f} sps "
                f"({sps / self.fs:.2f}x) | reconnects {c.reconnects}")


def main(argv=None) -> int:
    cfg = build_parser().parse_args(argv)
    try:
        st = Streamer(cfg)
    except InputError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_INPUT

    async def amain():
        loop, task = asyncio.get_running_loop(), asyncio.current_task()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda s=sig: (setattr(st, "signal", s), task.cancel()))
            except NotImplementedError:
                pass
        return await st.run()

    try:
        return asyncio.run(amain())
    except (KeyboardInterrupt, asyncio.CancelledError):  # e.g. a second Ctrl-C while saying bye
        return EXIT_SIGINT


if __name__ == "__main__":
    sys.exit(main())
