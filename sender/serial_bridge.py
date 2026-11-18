#!/usr/bin/env python3
"""CardioSense serial bridge (the hardware path, BRD "SerialSource"): Arduino + AD8232 -> WebSocket frames.

    python sender/serial_bridge.py --port /dev/ttyUSB0 --device dev-kit-01 --key <device key> --url ws://localhost:8000

Reads one text line per sample from the kit (firmware/cardiosense_ad8232.ino, the prototype's `HR,HRV,ECG`
lines, or the SparkFun AD8232 example), batches ~100 ms frames and sends them per docs/CONTRACT.md §2.1 with
source="serial". Timestamps are the sample counter (t0), never the wall clock. Deps: websockets, pyserial.
"""
from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cs_stream import (EXIT_INPUT, EXIT_OK, EXIT_SIGINT, EXIT_SIGTERM, ConnectionLost, FatalStreamError,  # noqa: E402
                       Status, StreamClient, fmt_time, frame_sizes, hello_msg)

BRIDGE_FIRMWARE = "serial-bridge-1.0"
# 10-bit ADC, AD8232 output biased at mid-rail -> zero 512. The gain is NOT calibrated on the kit: 225 adu/mV is
# nominal (SparkFun AD8232 total gain ~1100 V/V on a 5 V / 1023-count ADC). Amplitudes in mV are approximate;
# override with --adc-gain once measured (use ~341 for a 3.3 V reference).
ADC_ZERO, ADC_GAIN = 512, 225.0


class LineParser:
    """One serial text line -> ('sample', value, lead_off) | ('meta', {..}) | ('bad', None, None) | None (blank).

    Accepted sample lines:  `512`            single ADC value
                            `72,35,512`      legacy prototype `HR,HRV,ECG` (last field is the ECG sample)
                            `!`              electrodes off, no value (SparkFun example) -> last value is held
                            `!512` / `512!`  electrodes off, with the raw value
    Lines starting with `#` are firmware messages, e.g. `# cardiosense-ad8232 fw=1.0.0 fs=250 adc_bits=10`.
    """

    def __init__(self, adc_max: float = 1023):
        self.adc_max, self.last = adc_max, None

    def parse(self, raw: bytes):
        line = raw.decode("ascii", "replace").strip()
        if not line:
            return None
        if line.startswith("#"):
            return "meta", dict(tok.split("=", 1) for tok in line[1:].split() if "=" in tok), None
        lead_off = "!" in line
        fields = line.replace("!", " ").replace(",", " ").split()
        if not fields:
            return ("sample", self.last if self.last is not None else ADC_ZERO, True) if lead_off else None
        try:
            v = float(fields[-1])
        except ValueError:
            return "bad", None, None
        if not 0 <= v <= self.adc_max:            # e.g. a line truncated by a serial glitch
            return "bad", None, None
        self.last = int(v) if v.is_integer() else v
        return "sample", self.last, lead_off


class SerialReader(threading.Thread):
    """Reads the port in a thread, frames samples by count and hands frames to the asyncio side.

    A frame is (samples, lead_off, gap_before): `gap_before` = samples estimated lost while the port was
    disconnected, so the server records a gap instead of the stream silently closing up.
    """

    def __init__(self, port, baud, fs, frame_ms, emit, log, *, adc_max=1023, rate_window=10.0, opener=None):
        super().__init__(daemon=True, name="serial-reader")
        self.port, self.baud, self.fs, self.emit, self.log = port, baud, fs, emit, log
        self.parser, self.sizes = LineParser(adc_max), frame_sizes(fs, frame_ms)
        self.rate_window, self.opener = rate_window, opener or self._open
        self.stop_flag = threading.Event()
        self.banner = threading.Event()
        self.meta: dict = {}
        self.measured_fs = None
        self.total = self.bad = self.reopens = self.rate_warnings = 0
        self.lead_off = False
        self.last_sample_t = None                # monotonic time of the last sample (for gap estimates)

    def _open(self):
        import serial  # pyserial; imported here so the rest of the sender works without it
        ser = serial.serial_for_url(self.port, baudrate=self.baud, timeout=0.5)
        ser.reset_input_buffer()                 # samples buffered by the OS before we opened are not live
        return ser

    def run(self):
        delay, opened_before = 0.5, False
        while not self.stop_flag.is_set():
            try:
                ser = self.opener()
            except Exception as e:  # SerialException / OSError: port missing, busy, unplugged
                self.log(f"serial: cannot open {self.port} ({e}); retry in {delay:.1f}s")
                self.stop_flag.wait(delay)
                delay = min(delay * 2, 10.0)
                continue
            delay = 0.5
            gap = round((time.monotonic() - self.last_sample_t) * self.fs) if self.last_sample_t else 0
            if opened_before:
                self.reopens += 1
                self.log(f"serial: reconnected to {self.port} (~{gap} samples lost -> gap)")
            else:
                self.log(f"serial: opened {self.port} @ {self.baud} baud")
            opened_before = True
            try:
                self._pump(ser, gap)
            except Exception as e:
                self.log(f"serial: read error ({e}); reopening")
            finally:
                try:
                    ser.close()
                except Exception:
                    pass
        self.emit(None)

    def _pump(self, ser, gap: int):
        try:
            ser.write(b"?")                      # ask the firmware to (re)print its banner
        except Exception:
            pass
        buf, first_line = b"", True
        frame, lead_off, need = [], False, next(self.sizes)
        win_t = win_n = None                     # rate window start; skip the first second (stale OS buffer)
        t_open = last_data = time.monotonic()
        while not self.stop_flag.is_set():
            data = ser.read(ser.in_waiting or 1)
            now = time.monotonic()
            if not data:
                if now - last_data > 3.0:
                    self.log("serial: no data for 3 s (board unplugged, wrong --port/--baud, or firmware not running?)")
                    last_data = now
                continue
            last_data = now
            buf += data
            *lines, buf = buf.split(b"\n")
            for raw in lines:
                if first_line:                   # the first line after opening is usually a fragment
                    first_line = False
                    if not raw.lstrip().startswith(b"#"):
                        continue
                got = self.parser.parse(raw)
                if got is None:
                    continue
                kind, value, lo = got
                if kind == "meta":
                    self.meta.update(value)
                    self.banner.set()
                    if "overrun" in value:
                        self.log(f"WARNING: firmware missed {value['overrun']} sample slots (serial too slow for fs?)")
                    continue
                if kind == "bad":
                    self.bad += 1
                    continue
                frame.append(value)
                lead_off |= lo
                self.total += 1
                self.last_sample_t = now
                if len(frame) >= need:
                    self.lead_off = lead_off
                    self.emit((frame, lead_off, gap))
                    frame, lead_off, gap, need = [], False, 0, next(self.sizes)
            if win_t is None:
                if now - t_open >= 1.0:
                    win_t, win_n = now, self.total
            elif now - win_t >= self.rate_window:
                self.measured_fs = (self.total - win_n) / (now - win_t)
                if abs(self.measured_fs - self.fs) > 0.02 * self.fs:
                    self.rate_warnings += 1
                    self.log(f"WARNING: measured sample rate {self.measured_fs:.1f} Hz differs from --fs {self.fs:g} Hz "
                             f"by {100 * (self.measured_fs / self.fs - 1):+.1f}% - timing, HR and beat labels will be "
                             "wrong; fix the firmware rate or pass the real --fs")
                win_t, win_n = now, self.total
        if frame:                                # flush the partial frame on shutdown/reopen
            self.emit((frame, lead_off, gap))


class Bridge:
    def __init__(self, cfg, status: Status | None = None, opener=None):
        self.cfg, self.status, self.opener = cfg, status or Status(cfg.quiet), opener
        self.tag = f"[{cfg.device}]"
        self.reader = self.client = None
        self.signal = None
        self.dropped = 0

    def ev(self, msg: str):
        self.status.event(f"{self.tag} {msg}")

    async def run(self) -> int:
        cfg, loop = self.cfg, asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()

        def emit(frame):                         # called from the reader thread
            try:
                loop.call_soon_threadsafe(q.put_nowait, frame)
            except RuntimeError:                 # event loop already closed during shutdown
                pass

        self.reader = r = SerialReader(cfg.port, cfg.baud, cfg.fs, cfg.frame_ms,
                                       emit=emit, log=self.ev,
                                       adc_max=2 ** cfg.adc_bits - 1, rate_window=cfg.rate_window, opener=self.opener)
        r.start()
        code, t_start = EXIT_OK, time.monotonic()
        try:
            await asyncio.to_thread(r.banner.wait, cfg.banner_wait)   # Uno/Nano reset on open, then print the banner
            fw = r.meta.get("fw")
            if fw:
                self.ev(f"firmware {fw} reports fs={r.meta.get('fs', '?')} Hz")
                if "fs" in r.meta and abs(float(r.meta["fs"]) - cfg.fs) > 0.02 * cfg.fs:
                    self.ev(f"WARNING: firmware reports {r.meta['fs']} Hz but --fs is {cfg.fs:g}")
            else:
                self.ev("no firmware banner (legacy sketch?); assuming --fs is right, will verify by measurement")
            hello = hello_msg(cfg.device, cfg.fs, "serial", {"zero": cfg.adc_zero, "gain": cfg.adc_gain, "units": "adu"},
                              firmware=f"cardiosense-ad8232-{fw}" if fw else BRIDGE_FIRMWARE)
            self.client = c = StreamClient(cfg.url, cfg.device, cfg.key, hello, max_retries=cfg.max_retries, log=self.ev)
            await self._connect(q, first=True)
            while (item := await q.get()) is not None:
                samples, lead_off, gap = item
                if gap:
                    c.skip(gap)
                try:
                    await c.send_frame(samples, lead_off)
                except ConnectionLost as e:
                    self.ev(f"connection lost ({e}); reconnecting")
                    self.dropped += len(samples)
                    await self._connect(q, first=False)
                self.status.update(lambda: self._line(t_start))
            await c.close(bye=True)
        except asyncio.CancelledError:
            code = EXIT_SIGTERM if self.signal == signal.SIGTERM else EXIT_SIGINT
            self.ev("interrupted; sending bye")
            if self.client:
                try:
                    await asyncio.wait_for(self.client.close(bye=True), 3)
                except Exception:
                    pass
        except FatalStreamError as e:
            code = e.code
            print(f"{self.tag} error: {e}", file=sys.stderr, flush=True)
            await self.client.close(bye=False)
        finally:
            r.stop_flag.set()
            await asyncio.to_thread(r.join, 2.0)
        c = self.client
        self.ev(f"done: {r.total} samples read, {c.frames if c else 0} frames sent, measured "
                f"{r.measured_fs or 0:.1f} Hz, ws reconnects {c.reconnects if c else 0}, serial reconnects {r.reopens}, "
                f"bad lines {r.bad}, dropped while offline {self.dropped} samples, exit {code}")
        return code

    async def _connect(self, q: asyncio.Queue, first: bool):
        ack = await self.client.connect()
        while not first and not q.empty():       # live source: samples taken while offline are dropped, not burst
            item = q.get_nowait()
            if item is None:
                q.put_nowait(None)
                break
            self.dropped += len(item[0])
        self.ev(f"{'connected' if first else 'reconnected (new hello, new session, t0 restarts at 0)'}: "
                f"session {ack.get('session_id')} model_loaded={ack.get('model_loaded')}")

    def _line(self, t_start: float) -> str:
        r, c = self.reader, self.client
        rate = f"{r.measured_fs:.1f} Hz" if r.measured_fs else "measuring"
        return (f"{self.tag} {fmt_time(c.t0 / self.cfg.fs)} signal | {c.frames} frames | {rate} "
                f"| lead-off {'YES' if r.lead_off else 'no'} | reconnects ws {c.reconnects} serial {r.reopens} "
                f"| bad lines {r.bad}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Bridge an Arduino + AD8232 serial stream to CardioSense.",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--port", required=True, help="serial port, e.g. /dev/ttyUSB0, /dev/ttyACM0, COM5")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--fs", type=float, default=250.0, help="device sample rate in Hz (60-250)")
    ap.add_argument("--device", required=True, help="paired device id")
    ap.add_argument("--key", required=True, help="device key from pairing")
    ap.add_argument("--url", default="ws://localhost:8000", help="backend base URL")
    ap.add_argument("--frame-ms", type=float, default=100.0)
    ap.add_argument("--adc-bits", type=int, default=10, help="ADC resolution (values outside the range are rejected)")
    ap.add_argument("--adc-zero", type=float, default=ADC_ZERO, help="ADC baseline (mid-rail)")
    ap.add_argument("--adc-gain", type=float, default=ADC_GAIN, help="ADC units per mV - nominal, not calibrated")
    ap.add_argument("--max-retries", type=int, default=0, help="consecutive failed WebSocket connects before giving up (0 = forever)")
    ap.add_argument("--rate-window", type=float, default=10.0, help="seconds per sample-rate measurement")
    ap.add_argument("--banner-wait", type=float, default=2.5, help="seconds to wait for the firmware banner")
    ap.add_argument("--quiet", action="store_true")
    return ap


def main(argv=None) -> int:
    cfg = build_parser().parse_args(argv)
    if cfg.fs <= 0 or cfg.frame_ms <= 0 or cfg.adc_gain <= 0:
        print("error: --fs, --frame-ms and --adc-gain must be > 0", file=sys.stderr)
        return EXIT_INPUT
    bridge = Bridge(cfg)

    async def amain():
        loop, task = asyncio.get_running_loop(), asyncio.current_task()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda s=sig: (setattr(bridge, "signal", s), task.cancel()))
            except NotImplementedError:
                pass
        return await bridge.run()

    try:
        return asyncio.run(amain())
    except (KeyboardInterrupt, asyncio.CancelledError):
        return EXIT_SIGINT


if __name__ == "__main__":
    sys.exit(main())
