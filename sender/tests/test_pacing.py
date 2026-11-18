"""Pacing accuracy: real clock at speed 1 and 20 (within +-2% over a 20 s window), and drift/catch-up on a fake clock."""
import asyncio
import random

import numpy as np
import pytest
from conftest import stream

from cs_stream import Pacer

FS = 360


def big_record(tmp_path, seconds):
    n = seconds * FS
    p = tmp_path / "901.csv"
    p.write_text("'sample #','MLII','V1'\n" + "".join(f"{i},{1000 + i % 50},1024\n" for i in range(n)))
    return p


@pytest.mark.parametrize("speed,signal_s", [(1, 20), (20, 400)])     # both are a 20 s wall-clock window
def test_real_time_pacing_within_2_percent(tmp_path, speed, signal_s):
    code, st, srv = asyncio.run(stream(big_record(tmp_path, signal_s), "--speed", speed))
    (s,) = srv.sessions
    t, end = np.array(s.arrivals).T
    wall = t[-1] - t[0]
    rate = (end[-1] - end[0]) / wall                                  # samples per wall second, as received
    err_pct = 100 * (rate / (FS * speed) - 1)
    due = t[0] + (end - end[0]) / (FS * speed)                        # ideal arrival of every frame
    jitter_ms = 1000 * (t - due)
    print(f"\n[pacing] speed={speed}x: {s.frames} frames over {wall:.3f}s wall, {rate:.2f} samples/s "
          f"(target {FS * speed}), error {err_pct:+.3f}%, frame lateness p50 {np.median(jitter_ms):.2f} ms "
          f"p99 {np.percentile(jitter_ms, 99):.2f} ms max {jitter_ms.max():.2f} ms")
    assert code == 0 and s.samples == signal_s * FS and not s.gaps
    assert abs(err_pct) < 2.0
    assert abs(wall - (signal_s - 0.1) / speed) < 0.02 * signal_s / speed
    assert np.percentile(np.abs(jitter_ms), 99) < 100                 # no frame is ever far off its deadline


class FakeClock:
    """Simulated monotonic clock whose sleep always overshoots (like a loaded event loop)."""

    def __init__(self, overshoot=(0.0005, 0.004)):
        self.now, self.overshoot, self.sleeps = 1000.0, overshoot, 0

    def clock(self):
        return self.now

    async def sleep(self, d):
        self.sleeps += 1
        self.now += d + random.uniform(*self.overshoot)


def test_no_drift_over_30_minutes_despite_sleep_overshoot():
    async def go():
        fc = FakeClock()
        pacer = Pacer(FS, 1.0, clock=fc.clock, sleep=fc.sleep)
        t_start, n = fc.now, 0
        for _ in range(18000):                                        # 30 min of 100 ms frames
            n += 36
            await pacer.wait(n)
            fc.now += 0.0003                                          # cost of building/sending the frame
        return fc.now - t_start, fc.sleeps

    elapsed, sleeps = asyncio.run(go())
    print(f"\n[pacing] simulated 30 min: elapsed {elapsed:.4f}s vs 1800s (error {1000 * (elapsed - 1800):+.2f} ms); "
          f"a naive sleep(0.1)-per-frame loop would be ~{18000 * 0.00255:.0f}s late")
    assert sleeps == 18000 and abs(elapsed - 1800) < 0.01             # bounded by one overshoot, not accumulated


def test_catches_up_after_a_stall_without_sleeping():
    async def go():
        fc = FakeClock(overshoot=(0, 0))
        pacer = Pacer(250, 2.0, clock=fc.clock, sleep=fc.sleep)       # 500 samples/s
        t0 = fc.now
        await pacer.wait(50)
        assert fc.now - t0 == pytest.approx(0.1)
        fc.now += 1.0                                                 # stall (GC, Wi-Fi hiccup, laptop lid)
        before = fc.sleeps
        for n in range(100, 551, 50):                                 # the 9 overdue frames go out back-to-back
            await pacer.wait(n)
        assert fc.sleeps == before and pacer.lag(550) == pytest.approx(0.0)
        await pacer.wait(600)                                         # then back on the original schedule
        assert fc.now - t0 == pytest.approx(1.2) and fc.sleeps == before + 1
        pacer.reset(600)                                              # reconnect: re-anchor instead of bursting
        fc.now += 5
        pacer.reset(600)
        await pacer.wait(650)
        assert fc.now - t0 == pytest.approx(6.3)
        unpaced = Pacer(250, 0, clock=fc.clock, sleep=fc.sleep)       # --speed 0
        await unpaced.wait(10 ** 9)
        assert fc.sleeps == before + 2
    asyncio.run(go())
