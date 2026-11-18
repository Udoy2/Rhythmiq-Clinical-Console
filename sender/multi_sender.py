#!/usr/bin/env python3
"""Run N replay streams concurrently to distinct devices (BRD: 50 concurrent streams / soak test).

    python sender/multi_sender.py --files 'data/records/*.csv' -n 50 --url ws://localhost:8000 \
        --device-template 'dev-soak-{i:03d}' --key-template 'soak-key-{i:03d}' --loop --duration 14400

Records are assigned round-robin; every stream is a stream_sender.Streamer in one event loop. Templates take
{i} (0-based stream number) and {record} (file stem). Any other stream_sender option (--speed, --start,
--frame-ms, --no-annotations, --max-retries, ...) is passed through to every stream.
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stream_sender  # noqa: E402
from cs_stream import EXIT_INPUT, EXIT_OK, EXIT_SIGINT, EXIT_SIGTERM, Status, fmt_time  # noqa: E402


def expand(patterns: list[str]) -> list[str]:
    files = []
    for p in patterns:
        files += sorted(glob.glob(p)) or [p]
    return [f for f in files if not f.endswith("annotations.txt")]


def build(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog="Other options are passed to every "
                                 "stream; see stream_sender.py --help.")
    ap.add_argument("--files", nargs="+", required=True, help="record files and/or globs")
    ap.add_argument("-n", "--streams", type=int, default=None, help="number of streams (default: one per file)")
    ap.add_argument("--device-template", default="dev-demo-{record}", help="device id per stream")
    ap.add_argument("--key-template", default="demo-key-{record}", help="device key per stream")
    ap.add_argument("--stagger", type=float, default=0.05, help="seconds between stream starts")
    ap.add_argument("--report-every", type=float, default=5.0, help="seconds between aggregate lines when piped")
    ap.add_argument("--verbose", action="store_true", help="also print per-stream events")
    args, rest = ap.parse_known_args(argv)
    files = expand(args.files)
    n = args.streams or len(files)
    if not files or n < 1:
        ap.error("no record files matched")
    cfgs = []
    for i in range(n):
        f = files[i % len(files)]
        fmt = {"i": i, "record": Path(f).stem}
        cfgs.append(stream_sender.build_parser().parse_args(
            ["--file", f, "--device", args.device_template.format(**fmt), "--key", args.key_template.format(**fmt), *rest]))
    devices = [c.device for c in cfgs]
    if len(set(devices)) != len(devices):
        ap.error("device ids are not distinct (a second connection for a device ends the first session): "
                 "use {i} in --device-template or fewer streams")
    return args, cfgs


async def run(args, cfgs, status: Status | None = None) -> tuple[int, list]:
    status = status or Status(cfgs[0].quiet, every=args.report_every)
    per_stream = Status(quiet=not args.verbose)
    streams = [stream_sender.Streamer(c, per_stream) for c in cfgs]
    t_start = time.monotonic()
    prev = [t_start, 0, 0]

    def line() -> str:
        now = time.monotonic()
        frames, samples = sum(s.client.frames for s in streams), sum(s.client.samples for s in streams)
        dt = max(now - prev[0], 1e-9)
        fps, sps = (frames - prev[1]) / dt, (samples - prev[2]) / dt
        prev[:] = [now, frames, samples]
        done = [s.exit_code for s in streams if s.exit_code is not None]
        live = sum(s.client.ws is not None for s in streams)
        return (f"[multi] {fmt_time(now - t_start)} wall | streams {live}/{len(streams)} live, {len(done)} done, "
                f"{sum(1 for c in done if c)} failed | {fps:.0f} frames/s {sps:.0f} samples/s | total {frames} frames "
                f"| reconnects {sum(s.client.reconnects for s in streams)}")

    async def one(i, s):
        await asyncio.sleep(i * args.stagger)
        return await s.run()

    async def report():
        while True:
            await asyncio.sleep(0.5)
            status.update(line)

    reporter = asyncio.create_task(report())
    tasks = [asyncio.create_task(one(i, s)) for i, s in enumerate(streams)]
    try:
        codes = await asyncio.gather(*tasks)      # Streamer.run handles its own cancellation (sends bye)
    finally:
        reporter.cancel()
    wall = time.monotonic() - t_start
    frames, samples = sum(s.client.frames for s in streams), sum(s.client.samples for s in streams)
    failed = [(s.cfg.device, c) for s, c in zip(streams, codes) if c not in (EXIT_OK, EXIT_SIGINT, EXIT_SIGTERM)]
    status.event(f"[multi] done: {len(streams)} streams, {frames} frames, {samples} samples in {wall:.1f}s "
                 f"({frames / wall:.0f} frames/s, {samples / wall:.0f} samples/s aggregate), "
                 f"reconnects {sum(s.client.reconnects for s in streams)}, failed {len(failed)}"
                 + (f": {failed[:5]}" if failed else ""))
    return (max(codes) if any(codes) else EXIT_OK), streams


def main(argv=None) -> int:
    args, cfgs = build(argv)

    async def amain():
        loop = asyncio.get_running_loop()
        main_task = asyncio.ensure_future(run(args, cfgs))
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: [t.cancel() for t in asyncio.all_tasks(loop) if t is not main_task
                                                      and t is not asyncio.current_task()])
            except NotImplementedError:
                pass
        return (await main_task)[0]

    try:
        return asyncio.run(amain())
    except stream_sender.InputError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_INPUT
    except (KeyboardInterrupt, asyncio.CancelledError):
        return EXIT_SIGINT


if __name__ == "__main__":
    sys.exit(main())
