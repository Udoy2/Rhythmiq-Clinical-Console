"""End to end: device WebSocket -> pipeline core -> live WebSocket + PostgreSQL (FR-07..FR-12, FR-15).

Streams the first 90 s of MIT-BIH record 208 (360 Hz, ADC zero 1024, gain 200) faster than real time through
the real ingest endpoint and the real pipeline core, listens on the live endpoint and checks that beats,
rhythm windows and at least one alert with an evidence snapshot arrive live and are persisted.
"""

from __future__ import annotations

import asyncio
import bisect
import time

import numpy as np
import pytest
from sqlalchemy import func, select

from streaming import FS, DeviceStream, LiveCollector, load_record

pytestmark = pytest.mark.asyncio(loop_scope="session")

SECONDS = 90
SPEED = 8.0  # x real time
HELLO = {"fs": FS, "firmware": "e2e-test", "source": "replay", "adc": {"zero": 1024, "gain": 200.0, "units": "adu"},
         "record": "208", "dataset": "MIT-BIH", "speed": SPEED}


def _pct(values: list[float], q: float) -> float:
    return float(np.percentile(np.asarray(values), q)) if values else float("nan")


async def test_stream_record_208_end_to_end(client, auth, app, ws_base, capsys):
    from cardiosense.db import session_factory
    from cardiosense.models import Alert, Beat, MonitoringSession, RhythmWindow, SessionGap, SignalChunk, User
    from cardiosense.services.seed import demo_device_key, ensure_demo_patient

    try:
        import cardiosense.core  # noqa: F401
    except Exception:
        pytest.skip("real pipeline core not importable; see test_ingest_mechanics for the stand-in run")

    clin = await auth.headers("clinician")
    async with session_factory()() as db:
        org_id = (await db.execute(select(User.org_id).where(User.email == "clinician@cardiosense.dev"))).scalar_one()
        patient, device = await ensure_demo_patient(db, org_id, "e2e208")
        await db.commit()
    pid = patient.id
    samples, annotations = await asyncio.to_thread(load_record, "208", SECONDS)
    assert len(samples) == SECONDS * FS

    app.state.ingest.latency.reset()
    async with LiveCollector(ws_base, pid, await auth.token("clinician")) as live:
        first = await live.wait_for(lambda m: m["type"] == "status")
        assert first["stream"] == "disconnected"

        stream = DeviceStream(ws_base, device.id, demo_device_key("e2e208"))
        ack = await stream.open(HELLO)
        assert ack["type"] == "ack" and ack["session_id"], ack
        session_id = ack["session_id"]
        started = time.perf_counter()
        await stream.send_signal(samples[: 30 * FS], annotations, speed=SPEED)
        # a duplicated frame (same seq) must be dropped, not processed twice
        await stream.frame(samples[30 * FS - 36: 30 * FS], seq=stream.seq - 1, t0=stream.t0 - 36, advance=False)
        rest = [{"sample": a["sample"] - 30 * FS, "symbol": a["symbol"]} for a in annotations if a["sample"] >= 30 * FS]
        await stream.send_signal(samples[30 * FS:], rest, speed=SPEED)
        send_wall = time.perf_counter() - started
        await stream.bye()
        await live.wait_for(lambda m: m["type"] == "session" and m["active"] is False, timeout=60)
        await asyncio.sleep(0.2)
        messages = list(live.messages)

    await app.state.writer.drain()
    await app.state.notifier.wait_idle()

    by_type: dict[str, list[dict]] = {}
    for _, m in messages:
        by_type.setdefault(m["type"], []).append(m)
    sessions, beats, windows = by_type["session"], by_type.get("beat", []), by_type.get("window", [])
    alerts, signals = by_type.get("alert", []), by_type.get("signal", [])

    # ---- live feed (CONTRACT §2.2)
    opened = sessions[0]
    assert opened["active"] is True and opened["session_id"] == session_id and opened["patient_id"] == pid
    assert opened["fs"] == FS and opened["source"] == "replay" and opened["record"] == "208"
    assert opened["dataset"] == "MIT-BIH" and opened["rule_version"].startswith("rules-")
    assert any(m["stream"] == "connected" for m in by_type["status"])
    assert by_type["status"][-1]["stream"] == "disconnected" and by_type["status"][-1]["last_sample_time"]

    assert sum(len(m["mv"]) for m in signals) >= (SECONDS - 2) * FS, "display signal missing"
    assert all(m["fs"] == FS for m in signals)
    assert len(beats) >= 100, f"only {len(beats)} beats in {SECONDS}s of record 208"
    assert [b["sample"] for b in beats] == sorted(b["sample"] for b in beats)
    for b in beats:
        assert b["label"] in ("N", "S", "V", "F", "Unknown") and set(b["probs"]) == {"N", "S", "V", "F"}
        assert abs(b["time_s"] - b["sample"] / FS) < 0.01
    with_truth = [b for b in beats if b["truth"] is not None]
    assert len(with_truth) >= 0.8 * len(beats), "replay ground truth was not matched to beats"
    assert 8 <= len(windows) <= 9
    for w in windows:
        assert w["id"] and w["end_s"] > w["start_s"] and {"score", "tier", "factors"} <= set(w["risk"])
        assert {"hr_bpm", "findings", "s_burden_pct", "v_burden_pct", "sqi", "beat_counts", "rule_version"} <= set(w)
    assert alerts, "no alert was raised for record 208"
    alert = alerts[0]["alert"]
    assert alert["patient_id"] == pid and alert["session_id"] == session_id and alert["state"] == "open"
    assert alert["tier"] in ("amber", "orange", "red") and alert["rule_id"] and alert["title"]
    evidence = alert["evidence"]
    assert len(evidence["strip_mv"]) > evidence["fs"] and evidence["beats"] and evidence["rule_version"]
    assert evidence["end_s"] > evidence["start_s"]

    # ---- persistence (PostgreSQL)
    async with session_factory()() as db:
        n_beats = await db.scalar(select(func.count()).select_from(Beat).where(Beat.session_id == session_id))
        n_windows = await db.scalar(select(func.count()).select_from(RhythmWindow).where(RhythmWindow.session_id == session_id))
        n_gaps = await db.scalar(select(func.count()).select_from(SessionGap).where(SessionGap.session_id == session_id))
        rows = (await db.execute(select(Alert).where(Alert.session_id == session_id))).scalars().all()
        stored = {k: n for k, n in await db.execute(
            select(SignalChunk.kind, func.sum(SignalChunk.n)).where(SignalChunk.session_id == session_id)
            .group_by(SignalChunk.kind))}
        session = await db.get(MonitoringSession, session_id)
    assert n_beats == len(beats) and n_windows == len(windows) and n_gaps == 0
    assert len(rows) >= 1 and {a["alert"]["id"] for a in alerts} == {r.id for r in rows}
    for row in rows:
        assert row.evidence["strip_mv"] and row.rule_version and row.is_demo and row.rule_id not in ("LEAD_OFF", "LOW_SQI")
    assert stored["raw"] == SECONDS * FS and stored["display"] == sum(len(m["mv"]) for m in signals)
    assert session.ended_at is not None and session.is_demo and session.stats["beats"] == len(beats)
    assert session.stats["duplicates_dropped"] == 1 and session.stats["end_reason"] == "bye"
    assert session.stats["truth_agreement_pct"] is not None

    # ---- REST views of the same data
    got = (await client.get(f"/sessions/{session_id}", headers=clin)).json()
    assert got["active"] is False and got["summary"]["windows"] == len(windows) and got["duration_s"] == SECONDS
    ranged = (await client.get(f"/sessions/{session_id}/beats", params={"from": 10, "to": 20}, headers=clin)).json()
    assert ranged == [{k: b[k] for k in ranged[0]} for b in beats if 10 * FS <= b["sample"] <= 20 * FS]
    sig = (await client.get(f"/sessions/{session_id}/signal", params={"from": 5, "to": 15}, headers=clin)).json()
    assert sig["fs"] == FS and sig["t0"] == 5 * FS and len(sig["mv"]) == 10 * FS and None not in sig["mv"]
    live_mv = {m["t0"] + i: v for m in signals for i, v in enumerate(m["mv"])}
    assert all(abs(sig["mv"][i] - live_mv[5 * FS + i]) < 1e-3 for i in range(0, 10 * FS, 97)), "replay != live signal"
    events = (await client.get(f"/sessions/{session_id}/events", headers=clin)).json()
    assert {e["kind"] for e in events} >= {"window", "alert"}
    p = (await client.get(f"/patients/{pid}", headers=clin)).json()
    assert p["signal_status"] == "offline" and p["active_session_id"] is None and p["open_alerts"] >= 1
    assert p["risk_score"] == windows[-1]["risk"]["score"] and p["last_event"]["label"]
    detail = (await client.get(f"/alerts/{rows[0].id}", headers=clin)).json()
    assert any(d["channel"] == "in_app" and d["status"] == "delivered" for d in detail["deliveries"])
    csv = await client.get(f"/sessions/{session_id}/export.csv", headers=clin)
    assert csv.status_code == 200 and csv.text.count("\n") >= len(beats) + 6 and "not a medical device" in csv.text

    # ---- latency: server-side (frame receive -> broadcast) and client-observed (frame sent -> beat received)
    health = (await client.get("/health")).json()
    lat = health["latency_ms"]
    ends = [e for e, _ in stream.sent_at]
    observed = []
    for arrived, m in messages:
        if m["type"] == "beat":
            i = bisect.bisect_right(ends, m["sample"])
            if i < len(ends):
                observed.append((arrived - stream.sent_at[i][1]) * 1000)
    with capsys.disabled():
        print(f"\n[e2e] record 208: {SECONDS}s streamed at {SPEED:g}x in {send_wall:.1f}s wall; "
              f"{len(beats)} beats, {len(windows)} windows, {len(rows)} alert(s) "
              f"[{', '.join(sorted({r.rule_id + '/' + r.tier for r in rows}))}], "
              f"truth agreement {session.stats['truth_agreement_pct']}%")
        for name in ("push_ms", "frame_to_beat_broadcast_ms", "frame_to_alert_broadcast_ms",
                     "rpeak_frame_to_beat_broadcast_ms"):
            if name in lat:
                print(f"[e2e] server {name}: p50={lat[name]['p50']} p95={lat[name]['p95']} max={lat[name]['max']} "
                      f"(n={lat[name]['n']})")
        print(f"[e2e] client-observed R-peak frame sent -> beat on live WS: p50={_pct(observed, 50):.1f} ms "
              f"p95={_pct(observed, 95):.1f} ms (stream time compressed {SPEED:g}x)")
    assert lat["frame_to_beat_broadcast_ms"]["p95"] < 500, lat  # FR-07: p95 receive < 500 ms
    assert lat["rpeak_frame_to_beat_broadcast_ms"]["p95"] < 3000, lat  # NFR-01 budget
    assert health["writer"]["failed_ops"] == 0 and health["writer"]["queue_depth"] == 0


async def test_demo_replay_spawns_the_sender(client, auth, app, demo_patients):
    """POST /demo/replay runs the real sender CLI as a subprocess against this server."""
    from cardiosense.config import get_settings

    if not get_settings().sender_script.is_file() or not app.state.pipeline.available:
        pytest.skip("sender CLI or pipeline core not available")
    clin = await auth.headers("clinician")
    records = (await client.get("/demo/records", headers=clin)).json()
    assert {"100", "208", "223"} <= {r["record"] for r in records}
    rec = next(r for r in records if r["record"] == "100")
    assert rec["dataset"] == "MIT-BIH" and rec["fs"] == 360 and rec["duration_s"] > 1700 and rec["truth_counts"]["N"] > 1000
    assert rec["patient_id"] == demo_patients["100"]["id"]

    # demo data can never be streamed into a real patient record
    real = (await client.post("/patients", headers=clin, json={"name": "Real Person", "consent": True})).json()
    refused = await client.post("/demo/replay", headers=clin, json={"record": "100", "dataset": "MIT-BIH", "speed": 1,
                                                                   "patient_id": real["id"]})
    assert refused.status_code == 400
    assert (await client.post("/demo/replay", headers=clin, json={"record": "999", "dataset": "MIT-BIH", "speed": 1})).status_code == 404

    pid = demo_patients["100"]["id"]
    r = await client.post("/demo/replay", headers=clin, json={"record": "100", "dataset": "MIT-BIH", "speed": 30,
                                                             "duration_s": 25})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "started" and r.json()["patient_id"] == pid and r.json()["device_id"] == "dev-demo-100"
    try:
        deadline = time.monotonic() + 90
        done = None
        while time.monotonic() < deadline and done is None:
            await asyncio.sleep(0.5)
            await app.state.writer.drain()
            for s in (await client.get(f"/patients/{pid}/sessions", headers=clin)).json():
                if s["source"] == "replay" and s["ended_at"] and s["summary"].get("beats", 0) > 0:
                    done = s
        assert done is not None, f"replay did not produce a session: {(await client.get('/demo/status', headers=clin)).json()}"
        assert done["record"] == "100" and done["is_demo"] and 20 <= done["duration_s"] <= 26
        assert done["summary"]["beat_counts"]["N"] >= 15
    finally:
        stop = await client.post("/demo/stop", headers=clin, json={"patient_id": pid})
        assert stop.status_code == 200
    assert app.state.demo.running_count() == 0
