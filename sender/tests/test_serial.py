"""Serial bridge: line parsing, and pty end-to-end (framing, lead-off, rate check, serial + WebSocket reconnect)."""
import asyncio
import os
import sys
import threading
import time

import pytest

import serial_bridge
from cs_stream import Status
from mock_server import MockServer
from serial_bridge import LineParser

pty = pytest.importorskip("pty")
pytest.importorskip("serial")


def test_line_parser_accepts_legacy_single_value_and_lead_off_markers():
    p = LineParser(adc_max=1023)
    assert p.parse(b"512\r\n") == ("sample", 512, False)
    assert p.parse(b"72,35,530\n") == ("sample", 530, False)              # legacy HR,HRV,ECG -> last field
    assert p.parse(b"!\r\n") == ("sample", 530, True)                     # SparkFun marker: hold last value
    assert p.parse(b"!498\n") == ("sample", 498, True) and p.parse(b"72,35,499!\n") == ("sample", 499, True)
    assert p.parse(b"\r\n") is None
    assert p.parse(b"51x2\n")[0] == "bad" and p.parse(b"5120\n")[0] == "bad" and p.parse(b"-3\n")[0] == "bad"
    assert p.parse(b"# cardiosense-ad8232 fw=1.0.0 fs=250 adc_bits=10\n") == \
        ("meta", {"fw": "1.0.0", "fs": "250", "adc_bits": "10"}, None)
    assert LineParser().parse(b"!\n") == ("sample", 512, True)            # nothing to hold yet -> mid-rail


class FakeKit(threading.Thread):
    """Plays the Arduino: writes one line per sample to a pty at a fixed rate from a monotonic schedule."""

    def __init__(self, rate, seconds, line, banner=True):
        super().__init__(daemon=True)
        self.master, slave = pty.openpty()
        self.port = os.ttyname(slave)
        self.rate, self.n, self.line, self.banner = rate, int(rate * seconds), line, banner
        self.sent = 0

    def run(self):
        time.sleep(0.3)                                                   # "bootloader"
        if self.banner:
            os.write(self.master, f"# cardiosense-ad8232 fw=1.0.0 fs={self.rate} adc_bits=10\r\n".encode())
        t0 = time.monotonic()
        for i in range(self.n):
            os.write(self.master, self.line(i).encode() + b"\r\n")
            self.sent += 1
            if (d := t0 + (i + 1) / self.rate - time.monotonic()) > 0:
                time.sleep(d)


async def bridge_run(kit, srv, *extra, until=None):
    cfg = serial_bridge.build_parser().parse_args(
        ["--port", kit.port, "--device", "dev-kit-01", "--key", "k", "--url", srv.url, "--quiet",
         "--rate-window", "1", *map(str, extra)])
    logs = []
    st = Status(quiet=True)
    st.event = logs.append
    b = serial_bridge.Bridge(cfg, st)
    task = asyncio.create_task(b.run())
    kit.start()
    await asyncio.to_thread(kit.join)
    await asyncio.sleep(0.4)
    if until:
        await until(b)
    task.cancel()
    code = await task
    await asyncio.sleep(0.05)
    return code, b, logs


def test_bridge_frames_lead_off_and_measured_rate():
    def line(i):                                                          # mixed formats; electrodes off 1.0-1.5 s
        if 250 <= i < 375:
            return "!"
        return f"72,35,{400 + i % 200}" if i % 2 else str(400 + i % 200)

    async def go():
        async with MockServer(keep_samples=True) as srv:
            return await bridge_run(FakeKit(250, 3.2, line), srv), srv

    (code, b, logs), srv = asyncio.run(go())
    (s,) = srv.sessions
    assert code == 130 and s.bye and not s.errors and not s.gaps
    assert s.hello == {"type": "hello", "device_id": "dev-kit-01", "fs": 250, "firmware": "cardiosense-ad8232-1.0.0",
                       "source": "serial", "adc": {"zero": 512, "gain": 225.0, "units": "adu"}}
    assert s.samples in (799, 800) and s.frames == 32                     # first line after open may be a fragment
    k = 800 - s.samples
    assert s.values[:250 - k] == [400 + i % 200 for i in range(k, 250)]
    assert set(s.values[250 - k:375 - k]) == {400 + 249 % 200}            # held value while electrodes are off
    assert s.lead_off_frames in (5, 6)                                    # 125 samples = 5 frames (+1 if straddling)
    assert b.reader.measured_fs == pytest.approx(250, rel=0.02) and b.reader.rate_warnings == 0
    assert b.reader.bad == 0 and not any("WARNING" in m for m in logs)
    print(f"\n[serial] measured {b.reader.measured_fs:.2f} Hz from a 250 Hz source")


def test_bridge_warns_when_actual_rate_differs_from_fs_and_handles_garbage():
    async def go():
        async with MockServer() as srv:
            kit = FakeKit(200, 3.3, lambda i: "garbage" if i % 100 == 50 else str(500 + i % 9), banner=False)
            return await bridge_run(kit, srv), srv

    (code, b, logs), srv = asyncio.run(go())
    assert b.reader.measured_fs == pytest.approx(198, rel=0.03) and b.reader.rate_warnings >= 1
    assert any("WARNING: measured sample rate" in m and "250" in m for m in logs)
    assert any("no firmware banner" in m for m in logs)
    assert 6 <= b.reader.bad <= 7 and srv.sessions[0].hello["firmware"] == "serial-bridge-1.0"


def test_bridge_reconnects_websocket_with_new_session():
    async def go():
        async with MockServer(drop_after=[5]) as srv:
            return await bridge_run(FakeKit(250, 3.0, lambda i: str(512 + i % 3)), srv), srv

    (code, b, logs), srv = asyncio.run(go())
    a, c = srv.sessions
    assert a.frames == 5 and c.first_t0 == 0 and not c.gaps and c.bye and b.client.reconnects == 1
    assert any("reconnected (new hello" in m for m in logs)
    assert a.samples + c.samples + b.dropped >= 700                       # live source: offline samples are dropped


def test_bridge_reopens_serial_port_and_reports_gap():
    """Unplug/replug: the opener fails for a while, then the stream resumes with a gap, same session."""
    import serial

    async def go():
        async with MockServer() as srv:
            kit = FakeKit(250, 3.0, lambda i: str(512))
            state = {"opens": 0}

            class Flaky:                                                  # wraps the real port; dies once after 1 s
                def __init__(self):
                    state["opens"] += 1
                    if state["opens"] == 2:                               # still unplugged on the first retry
                        raise serial.SerialException("could not open port")
                    self.ser = serial.serial_for_url(kit.port, baudrate=115200, timeout=0.5)
                    self.ser.reset_input_buffer()                         # what was sent meanwhile is lost
                    self.t = time.monotonic()
                    self.first = state["opens"] == 1
                in_waiting = property(lambda self: self.ser.in_waiting)
                write = lambda self, b: self.ser.write(b)   # noqa: E731
                close = lambda self: self.ser.close()       # noqa: E731

                def read(self, n):
                    if self.first and time.monotonic() - self.t > 1.0:
                        raise serial.SerialException("device disconnected")
                    return self.ser.read(n)

            cfg = serial_bridge.build_parser().parse_args(
                ["--port", kit.port, "--device", "dev-kit-01", "--key", "k", "--url", srv.url, "--quiet"])
            logs = []
            st = Status(quiet=True)
            st.event = logs.append
            b = serial_bridge.Bridge(cfg, st, opener=Flaky)
            task = asyncio.create_task(b.run())
            kit.start()
            await asyncio.to_thread(kit.join)
            await asyncio.sleep(0.4)
            task.cancel()
            await task
            await asyncio.sleep(0.05)
            return b, logs, srv

    b, logs, srv = asyncio.run(go())
    (s,) = srv.sessions
    assert b.reader.reopens == 1 and any("serial: reconnected" in m for m in logs)
    assert any("cannot open" in m for m in logs)
    (gap,) = s.gaps                                                       # ~0.5 s backoff at 250 Hz, never interpolated
    assert 100 <= gap["missing"] <= 250 and abs(s.samples + gap["missing"] - 750) <= 40
