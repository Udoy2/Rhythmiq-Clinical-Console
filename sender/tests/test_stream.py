"""End-to-end against the mock server: contract shape, annotation alignment, reconnect, faults, exit codes."""
import asyncio
import json
import os
import signal
import subprocess
import sys

import numpy as np
import pytest
import websockets
from conftest import REC208, REPO, SENDER, free_port, make_cfg, stream

import multi_sender
import stream_sender
from cs_stream import Status, frame_sizes
from mock_server import MockServer
from stream_sender import find_annotations, load_annotations

run = asyncio.run
needs208 = pytest.mark.skipif(not REC208.exists(), reason="data/records/208.csv not present")


@needs208
def test_contract_hello_frames_and_annotation_alignment_on_record_208():
    code, st, srv = run(stream(REC208, "--speed", 0, "--duration", 120, device="dev-demo-208", key="demo-key-208"))
    (s,) = srv.sessions
    assert code == 0 and s.bye and not s.errors and not s.gaps and s.duplicates == 0
    assert s.hello == {"type": "hello", "device_id": "dev-demo-208", "fs": 360, "firmware": "sender-1.0",
                       "source": "replay", "adc": {"zero": 1024, "gain": 200.0, "units": "adu"},
                       "record": "208", "dataset": "MIT-BIH", "speed": 0.0}
    assert s.frames == 1200 and s.samples == 43200 and s.first_t0 == 0       # 100 ms frames of 36 samples
    truth = np.loadtxt(REC208, delimiter=",", skiprows=1, max_rows=43200, dtype=int)[:, 1]
    assert s.values == truth.tolist() and all(isinstance(v, int) for v in s.values)
    idx, sym = load_annotations(find_annotations(REC208))
    assert s.annotations == [(int(i), c) for i, c in zip(idx, sym) if i < 43200]
    assert s.annotations[:2] == [(46, "F"), (209, "V")]


def test_start_offset_shifts_annotations_to_stream_time(mitbih_file):
    path, mlii, beats = mitbih_file
    code, st, srv = run(stream(path, "--speed", 0, "--start", 1.0, "--duration", 5))
    (s,) = srv.sessions
    assert code == 0 and s.values == mlii[360:2160].tolist()
    assert s.annotations == [(i - 360, c) for i, c in beats if 360 <= i < 2160]


def test_loop_wraps_file_with_continuous_t0_and_repeated_annotations(mitbih_file):
    path, mlii, beats = mitbih_file
    code, st, srv = run(stream(path, "--speed", 0, "--loop", "--duration", 25))      # file is 10 s
    (s,) = srv.sessions
    assert code == 0 and not s.gaps and s.samples == 9000
    assert s.values == (mlii.tolist() * 3)[:9000]
    assert s.annotations == [(i + k * 3600, c) for k in range(3) for i, c in beats if i + k * 3600 < 9000]


def test_all_annotations_and_no_annotations(mitbih_file):
    path, _, beats = mitbih_file
    _, _, srv = run(stream(path, "--speed", 0, "--all-annotations"))
    assert len(srv.sessions[0].annotations) == len(beats) + 2 and (2250, "+") in srv.sessions[0].annotations
    _, _, srv = run(stream(path, "--speed", 0, "--no-annotations"))
    assert srv.sessions[0].annotations == [] and srv.sessions[0].samples == 3600


def test_non_integer_frame_length_and_mv_samples(tmp_path):
    p = tmp_path / "mv.txt"
    vals = np.round(np.sin(np.arange(1000) / 5.0), 4)
    p.write_text("\n".join(f"{v:.4f}" for v in vals) + "\n")
    code, st, srv = run(stream(p, "--speed", 0, "--fs", 125))
    (s,) = srv.sessions
    assert code == 0 and "adc" not in s.hello and "dataset" not in s.hello and s.hello["fs"] == 125
    assert s.values == vals.tolist() and s.frames == 80 and not s.gaps      # 12/13-sample frames
    sizes = [n for n, _ in zip(frame_sizes(125, 100), range(80))]
    assert set(sizes) == {12, 13} and sum(sizes) == 1000


def test_reconnect_after_network_drop_starts_new_session(mitbih_file):
    path, mlii, beats = mitbih_file
    code, st, srv = run(stream(path, "--speed", 20, server_kw={"drop_after": [20]}))
    a, b = srv.sessions
    assert code == 0 and st.client.reconnects == 1 and st.client.connects == 2
    assert a.frames == 20 and not a.bye and b.bye and not b.errors
    assert b.first_t0 == 0 and not b.gaps and not a.gaps                # new hello -> new session -> t0 restarts
    assert a.session_id != b.session_id and b.hello == a.hello
    off = 3600 - b.samples                                              # session 2 resumes where the drop was noticed
    assert 720 <= off <= 720 + 5 * 36                                   # at most a few in-flight frames are lost
    assert b.values == mlii[off:].tolist()
    assert b.annotations == [(i - off, c) for i, c in beats if i >= off]
    assert st.client.seq == st.client.frames > a.frames + b.frames - 1  # seq never reused within the process


def test_waits_with_backoff_for_a_server_that_is_not_up_yet(mitbih_file):
    path, _, _ = mitbih_file

    async def go():
        port = free_port()
        st = stream_sender.Streamer(make_cfg(path, f"ws://127.0.0.1:{port}", "--speed", 0), Status(quiet=True))
        task = asyncio.create_task(st.run())
        await asyncio.sleep(1.2)                                        # 2 failed attempts (0.5 s, 1 s backoff)
        async with MockServer(port=port) as srv:
            code = await asyncio.wait_for(task, 10)
            return code, srv.sessions

    code, sessions = run(go())
    assert code == 0 and len(sessions) == 1 and sessions[0].samples == 3600


def test_fault_injection_lead_off_dropout_and_noise(mitbih_file):
    path, mlii, beats = mitbih_file
    code, st, srv = run(stream(path, "--speed", 0, "--lead-off-at", "2:1", "--dropout-at", "4:1.5",
                               "--noise-db", 6, "--noise-at", "7:2", "--seed", 1))
    (s,) = srv.sessions
    v = np.array(s.values)
    assert code == 0 and s.lead_off_frames == 10 and st.dropped == 540
    assert s.gaps == [{"at_sample": 1440, "missing": 540}]             # t0 jump, nothing interpolated
    assert s.samples == 3600 - 540
    f = lambda i: i if i < 1440 else i - 540                           # file index -> index in received values  # noqa: E731
    assert (v[720:1080] == 1024).all()                                 # electrodes off: flat at the ADC baseline
    assert (v[:720] == mlii[:720]).all() and (v[1080:1440] == mlii[1080:1440]).all()
    assert (v[f(1980):f(2520)] == mlii[1980:2520]).all()
    noise = v[f(2520):f(3240)] - mlii[2520:3240]
    snr = 20 * np.log10(mlii[:3600].std() / noise.std())
    assert abs(snr - 6) < 1.0 and (v[f(3240):] == mlii[3240:]).all()
    got = dict(s.annotations)
    assert all(not 720 <= i < 1080 and not 1440 <= i < 1980 for i in got)   # no truth in flat/dropped spans
    assert sorted(got) == [i for i, _ in beats if not 720 <= i < 1080 and not 1440 <= i < 1980]


def test_noise_whole_stream_stays_integer_adc(mitbih_file):
    path, mlii, _ = mitbih_file
    _, _, srv = run(stream(path, "--speed", 0, "--noise-db", 0, "--seed", 3))
    v = srv.sessions[0].values
    assert all(isinstance(x, int) for x in v)
    assert abs(20 * np.log10(mlii.std() / (np.array(v) - mlii).std())) < 1.0


def test_wrong_key_is_fatal_not_retried(mitbih_file):
    path, _, _ = mitbih_file
    code, st, srv = run(stream(path, "--speed", 0, server_kw={"keys": {"dev-test-1": "right"}}, key="wrong"))
    assert code == 3 and srv.rejected == 1 and not srv.sessions and st.client.connects == 0


def test_multi_sender_runs_distinct_devices_concurrently(mitbih_file):
    path, _, _ = mitbih_file

    async def go():
        async with MockServer() as srv:
            args, cfgs = multi_sender.build(["--files", str(path), "-n", "12", "--device-template", "dev-soak-{i:03d}",
                                             "--key-template", "key-{i}", "--url", srv.url, "--speed", "0", "--quiet"])
            code, streams = await multi_sender.run(args, cfgs)
            await asyncio.sleep(0.05)
            return code, srv

    code, srv = run(go())
    assert code == 0 and len(srv.sessions) == 12
    assert sorted(s.device for s in srv.sessions) == [f"dev-soak-{i:03d}" for i in range(12)]
    assert all(s.samples == 3600 and s.bye and not s.gaps and not s.errors for s in srv.sessions)
    with pytest.raises(SystemExit):                                     # duplicate device ids are refused
        multi_sender.build(["--files", str(path), "-n", "2", "--url", "ws://x"])


# ---- the CLI as the backend's demo-replay endpoint spawns it -------------------------------------------------

def cli(*args, **kw):
    return subprocess.Popen([sys.executable, "sender/stream_sender.py", *map(str, args)], cwd=REPO,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw)


@needs208
def test_canonical_command_and_exit_codes():
    async def go():
        async with MockServer(keys={"dev-demo-208": "demo-key-208"}) as srv:
            def canonical(*extra, key="demo-key-208"):
                p = cli("--file", "data/records/208.csv", "--device", "dev-demo-208", "--key", key,
                        "--url", srv.url, "--speed", 50, *extra)
                return p, asyncio.to_thread(p.communicate, timeout=30)
            p, done = canonical("--duration", 20)
            out, err = await done
            assert p.returncode == 0, err
            assert "session " + srv.sessions[0].session_id in out and "model_loaded=True" in out
            assert srv.sessions[0].samples == 7200 and srv.sessions[0].bye

            p, done = canonical(key="nope")
            out, err = await done
            assert p.returncode == 3 and "invalid device id or key" in err

            p, done = canonical()                                       # Ctrl-C / SIGTERM mid-stream -> bye, clean exit
            while len(srv.sessions) < 2 or srv.sessions[1].frames < 5:
                await asyncio.sleep(0.05)
            p.send_signal(signal.SIGINT)
            out, err = await done
            await asyncio.sleep(0.1)
            assert p.returncode == 130 and srv.sessions[1].bye and "Traceback" not in err

            p, done = canonical()
            while len(srv.sessions) < 3 or srv.sessions[2].frames < 5:
                await asyncio.sleep(0.05)
            p.send_signal(signal.SIGTERM)
            await done
            await asyncio.sleep(0.1)
            assert p.returncode == 143 and srv.sessions[2].bye
    run(go())


def test_exit_codes_for_bad_input_and_unreachable_server(mitbih_file):
    path, _, _ = mitbih_file
    p = cli("--file", "nope.csv", "--device", "d", "--key", "k")
    assert p.wait(20) == 1 and "no such file" in p.stderr.read()
    p = cli("--device", "d", "--key", "k")
    assert p.wait(20) == 2
    p = cli("--file", path, "--device", "d", "--key", "k", "--url", f"ws://127.0.0.1:{free_port()}", "--max-retries", 1)
    assert p.wait(20) == 4 and "gave up" in p.stderr.read()


# ---- the mock server itself enforces §2.1 ---------------------------------------------------------------------

def test_mock_server_validates_hello_and_frames():
    async def go():
        async with MockServer() as srv:
            async def session(*msgs, path="/v1/stream/dev-x?key=k"):
                replies = []
                async with websockets.connect(srv.url + path) as ws:
                    try:
                        for m in msgs:
                            await ws.send(json.dumps(m))
                    except websockets.ConnectionClosed:                 # rejected before we finished sending
                        pass
                    try:
                        async for raw in ws:
                            replies.append(json.loads(raw))
                    except websockets.ConnectionClosed:
                        pass
                return replies

            hello = {"type": "hello", "device_id": "dev-x", "fs": 250, "firmware": "t", "source": "serial",
                     "adc": {"zero": 512, "gain": 225.0, "units": "adu"}}
            fr = lambda seq, t0, n=25, **kw: {"type": "frame", "seq": seq, "device_id": "dev-x", "t0": t0, "fs": 250,  # noqa: E731
                                              "samples": [512] * n, "lead_off": False, "source": "serial", **kw}
            r = await session(hello, fr(0, 0), fr(1, 25), fr(1, 25), fr(2, 100, lead_off=True), {"type": "bye"})
            s = srv.sessions[-1]
            assert r == [{"type": "ack", "session_id": s.session_id, "model_loaded": True}]
            assert (s.frames, s.duplicates, s.lead_off_frames, s.bye) == (3, 1, 1, True)
            assert s.gaps == [{"at_sample": 50, "missing": 50}]

            assert (await session(hello, path="/v1/stream/dev-x"))[0]["type"] == "error"            # no key
            assert (await session({**hello, "device_id": "other"}))[0]["type"] == "error"
            assert (await session({**hello, "source": "usb"}))[0]["type"] == "error"
            assert (await session(fr(0, 0)))[0]["type"] == "error"                                  # frame before hello
            for bad in (fr(0, 0, fs=360), fr(0, 0, samples=[]), fr(0, 0, lead_off=1), {**fr(0, 0), "seq": "0"},
                        fr(0, 0, annotations=[{"sample": 99, "symbol": "N"}])):
                r = await session(hello, bad)
                assert [m["type"] for m in r] == ["ack", "error"], bad
            r = await session(hello, fr(0, 0), fr(1, 10))                                           # t0 goes backwards
            assert r[-1]["type"] == "error" and "overlaps" in r[-1]["detail"]
    run(go())
