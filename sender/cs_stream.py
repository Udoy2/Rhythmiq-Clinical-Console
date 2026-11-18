"""Shared WebSocket client, framing, pacing and status output for the CardioSense senders.

Implements the device side of CONTRACT §2.1 (`WS /v1/stream/{device_id}?key=`):
hello -> ack -> frames (seq, t0, samples, lead_off, annotations) -> bye.
Only depends on `websockets`; used by stream_sender.py, serial_bridge.py and multi_sender.py.
"""
from __future__ import annotations

import asyncio
import json
import random
import sys
import time
from urllib.parse import quote

import websockets
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

FIRMWARE = "sender-1.0"

# process exit codes shared by the CLIs
EXIT_OK, EXIT_INPUT, EXIT_USAGE, EXIT_REJECTED, EXIT_UNREACHABLE = 0, 1, 2, 3, 4
EXIT_SIGINT, EXIT_SIGTERM = 130, 143


class FatalStreamError(Exception):
    """Not worth retrying: server rejected us (auth/consent/protocol) or retries are exhausted."""

    def __init__(self, msg: str, code: int = EXIT_REJECTED):
        super().__init__(msg)
        self.code = code


class ConnectionLost(Exception):
    """The connection dropped mid-stream; call `StreamClient.connect()` again to start a new session."""


def stream_url(base: str, device: str, key: str) -> str:
    base = base.rstrip("/")
    if base.startswith("http"):  # http(s):// -> ws(s)://
        base = "ws" + base[4:]
    return f"{base}/v1/stream/{quote(device, safe='')}?key={quote(key, safe='')}"


def hello_msg(device: str, fs: float, source: str, adc: dict | None = None, *, firmware: str = FIRMWARE,
              record: str | None = None, dataset: str | None = None, speed: float | None = None) -> dict:
    """Build the §2.1 hello. `adc=None` means samples are already in mV (the key is omitted)."""
    msg = {"type": "hello", "device_id": device, "fs": _num(fs), "firmware": firmware, "source": source}
    if adc:
        msg["adc"] = {"zero": _num(adc["zero"]), "gain": float(adc["gain"]), "units": adc.get("units", "adu")}
    if record is not None:
        msg["record"] = str(record)
    if dataset is not None:
        msg["dataset"] = dataset
    if speed is not None:
        msg["speed"] = float(speed)
    return msg


def frame_sizes(fs: float, frame_ms: float):
    """Yield frame lengths so that frame k ends at round((k+1)*fs*frame_ms/1000): exact on average for any fs."""
    k = end = 0
    while True:
        k += 1
        nxt = max(end + 1, round(k * fs * frame_ms / 1000.0))
        yield nxt - end
        end = nxt


def _num(x):
    return int(x) if float(x).is_integer() else float(x)


def _short(e: BaseException) -> str:
    if isinstance(e, ConnectionRefusedError) or "Connect call failed" in str(e):
        return "connection refused"
    return (str(e) or type(e).__name__)[:120]


def _fatal_close(exc: ConnectionClosed) -> str | None:
    """Close codes that mean 'do not come back': policy violation or an application code (4000-4999)."""
    code = exc.rcvd.code if exc.rcvd else None
    if code == 1008 or (code is not None and 4000 <= code <= 4999):
        return f"server closed the stream ({code} {exc.rcvd.reason or ''})".rstrip()
    return None


class StreamClient:
    """One device stream. Owns `seq` (monotonic for the life of the process) and `t0` (sample index since the
    start of the current server session; restarts at 0 on every (re)connect because one connection = one session).
    """

    def __init__(self, url: str, device: str, key: str, hello: dict, *, max_retries: int = 8, log=print,
                 backoff: tuple[float, float] = (0.5, 10.0), ack_timeout: float = 10.0):
        self.url, self.device, self.hello = stream_url(url, device, key), device, hello
        self.fs, self.source = hello["fs"], hello["source"]
        self.max_retries, self.log, self.backoff, self.ack_timeout = max_retries, log, backoff, ack_timeout
        self.ws = None
        self.seq = self.t0 = 0
        self.frames = self.samples = self.reconnects = self.connects = 0
        self.session_id, self.model_loaded, self.server_error = None, None, None
        self._reader = None

    async def connect(self) -> dict:
        """(Re)connect with exponential backoff + jitter, send hello and wait for the ack. Returns the ack."""
        await self._drop()
        attempt = 0
        while True:
            try:
                ack = await self._open()
                break
            except (OSError, InvalidHandshake, ConnectionClosed, asyncio.TimeoutError) as e:
                if isinstance(e, InvalidStatus) and e.response.status_code in (400, 401, 403, 404):
                    raise FatalStreamError(f"server rejected the connection (HTTP {e.response.status_code}): "
                                           "check --device/--key and patient consent") from None
                if isinstance(e, ConnectionClosed) and _fatal_close(e):
                    raise FatalStreamError(_fatal_close(e)) from None
                attempt += 1
                if self.max_retries and attempt > self.max_retries:
                    raise FatalStreamError(f"gave up after {self.max_retries} retries: {_short(e)}",
                                           EXIT_UNREACHABLE) from None
                delay = min(self.backoff[1], self.backoff[0] * 2 ** (attempt - 1)) * random.uniform(0.8, 1.2)
                self.log(f"connect failed ({_short(e)}); retry {attempt} in {delay:.1f}s")
                await asyncio.sleep(delay)
        if self.connects:
            self.reconnects += 1
        self.connects += 1
        self.t0 = 0
        self.session_id, self.model_loaded = ack.get("session_id"), ack.get("model_loaded")
        return ack

    async def _open(self) -> dict:
        self.server_error = None
        ws = await websockets.connect(self.url, open_timeout=10, compression=None, max_size=2 ** 20)
        try:
            await ws.send(json.dumps(self.hello))
            msg = json.loads(await asyncio.wait_for(ws.recv(), self.ack_timeout))
        except BaseException:
            await ws.close()
            raise
        if msg.get("type") != "ack":
            await ws.close()
            raise FatalStreamError(f"server rejected hello: {msg.get('detail', msg)}")
        self.ws = ws
        self._reader = asyncio.create_task(self._read(ws))
        return msg

    async def _read(self, ws):
        """Drain server messages so an `error` is noticed and the receive queue never fills."""
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("type") == "error":
                    self.server_error = str(msg.get("detail", "error"))
        except ConnectionClosed:
            pass

    def skip(self, n: int):
        """Advance t0 without sending: the server sees a jump in t0 and records a gap (never interpolated)."""
        self.t0 += int(n)

    async def send_frame(self, samples: list, lead_off: bool = False, annotations=None):
        """Send one frame at the current t0. `annotations` = [(offset_in_frame, symbol)], sent as absolute indices.
        Raises ConnectionLost (state unchanged, so the same frame can be re-sent after `connect()`)."""
        if self.server_error:
            raise FatalStreamError(f"server error: {self.server_error}")
        if self.ws is None:
            raise ConnectionLost("not connected")
        msg = {"type": "frame", "seq": self.seq, "device_id": self.device, "t0": self.t0, "fs": self.fs,
               "samples": samples, "lead_off": bool(lead_off), "source": self.source}
        if annotations:
            msg["annotations"] = [{"sample": self.t0 + int(off), "symbol": sym} for off, sym in annotations]
        try:
            await self.ws.send(json.dumps(msg, separators=(",", ":")))  # awaits drain => back-pressure
        except ConnectionClosed as e:
            await asyncio.sleep(0)  # let the reader pick up a final `error` message
            fatal = (self.server_error and f"server error: {self.server_error}") or _fatal_close(e)
            if fatal:
                raise FatalStreamError(fatal) from None
            raise ConnectionLost(str(e) or "connection closed") from None
        self.seq += 1
        self.t0 += len(samples)
        self.frames += 1
        self.samples += len(samples)

    async def close(self, bye: bool = True):
        if self.ws is not None and bye:
            try:
                await self.ws.send('{"type":"bye"}')
            except ConnectionClosed:
                pass
        await self._drop()

    async def _drop(self):
        ws, self.ws = self.ws, None
        if ws is not None:
            try:
                await asyncio.wait_for(ws.close(), 3)
            except Exception:
                pass
        if self._reader is not None:
            self._reader.cancel()
            self._reader = None


class Pacer:
    """Deadline pacing: sample i is due at anchor + i/(fs*speed) on a monotonic clock.

    Deadlines are computed from the anchor, never from the previous frame, so sleep overshoot does not
    accumulate (no drift), and after a stall the overdue frames go out back-to-back until caught up.
    speed <= 0 disables pacing (as fast as the socket's back-pressure allows).
    """

    def __init__(self, fs: float, speed: float = 1.0, clock=time.monotonic, sleep=asyncio.sleep):
        self.rate, self.clock, self.sleep = fs * speed, clock, sleep
        self.reset()

    def reset(self, sample: int = 0):
        """Re-anchor: `sample` is due now (used at start and after a reconnect)."""
        self._t, self._i = self.clock(), sample

    def lag(self, sample: int) -> float:
        """Seconds we are behind (>0) or ahead (<0) of the deadline for `sample`."""
        return self.clock() - self._t - (sample - self._i) / self.rate if self.rate > 0 else 0.0

    async def wait(self, sample: int):
        if self.rate <= 0:
            await asyncio.sleep(0)               # unpaced: still yield so concurrent streams share the loop
        elif (delay := -self.lag(sample)) > 0:
            await self.sleep(delay)


class Status:
    """One-line progress on a TTY (carriage return); a plain line every `every` s when piped; silent if quiet."""

    def __init__(self, quiet: bool = False, every: float = 30.0, stream=None):
        self.quiet, self.stream = quiet, stream or sys.stdout
        self.tty = self.stream.isatty()
        self.every = 0.5 if self.tty else every
        self._last, self._dirty = (0.0 if self.tty else time.monotonic()), False

    def update(self, make_text, force: bool = False):
        """`make_text` is a callable so the text is only built when it is going to be printed."""
        now = time.monotonic()
        if self.quiet or (not force and now - self._last < self.every):
            return
        self._last = now
        text = make_text()
        if self.tty:
            self.stream.write("\r\x1b[2K" + text)
            self._dirty = True
        else:
            self.stream.write(text + "\n")
        self.stream.flush()

    def event(self, text: str):
        """A full log line (connects, reconnects, warnings, summary); printed even when piped."""
        if self.quiet:
            return
        if self._dirty:
            self.stream.write("\r\x1b[2K")
            self._dirty = False
        self.stream.write(text + "\n")
        self.stream.flush()


def fmt_time(seconds: float) -> str:
    m, s = divmod(max(0.0, seconds), 60)
    h, m = divmod(int(m), 60)
    return f"{h:02d}:{m:02d}:{s:04.1f}"


def parse_span(text: str) -> tuple[float, float]:
    """'T:DUR' (seconds) -> (start, end)."""
    try:
        t, dur = (float(p) for p in text.split(":"))
    except ValueError:
        raise ValueError(f"expected T:DUR in seconds, got {text!r}") from None
    if t < 0 or dur <= 0:
        raise ValueError(f"T must be >= 0 and DUR > 0, got {text!r}")
    return t, t + dur
