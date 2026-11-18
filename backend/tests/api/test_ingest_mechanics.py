"""Ingest mechanics on a deterministic stand-in core (same CONTRACT §3 interface, see fake_core.py):
sequence de-duplication, gap marking, live backlog, alert de-duplication, device tasks, session takeover."""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
import websockets
from sqlalchemy import func, select

import fake_core
from streaming import FS, DeviceStream, LiveCollector

pytestmark = pytest.mark.asyncio(loop_scope="session")

HELLO = {"fs": FS, "firmware": "mech-test", "source": "replay", "adc": {"zero": 1024, "gain": 200.0},
         "record": "mech", "dataset": "TEST"}
SECOND = [1024] * FS


@pytest_asyncio.fixture(loop_scope="session")
async def fake_pipeline(app):
    """Swap the processor factory for the stand-in for one test."""
    provider = app.state.pipeline
    saved = (provider._factory, provider.rule_version, provider.classifier, provider._model_version_override,
             provider.available, provider.error)
    provider.install(fake_core.FakeStreamProcessor, rule_version=fake_core.RULE_VERSION,
                     model_version=fake_core.MODEL_VERSION)
    yield provider
    (provider._factory, provider.rule_version, provider.classifier, provider._model_version_override,
     provider.available, provider.error) = saved


async def _demo_target(tag: str):
    from cardiosense.db import session_factory
    from cardiosense.models import User
    from cardiosense.services.seed import demo_device_key, ensure_demo_patient

    async with session_factory()() as db:
        org_id = (await db.execute(select(User.org_id).where(User.email == "clinician@cardiosense.dev"))).scalar_one()
        patient, device = await ensure_demo_patient(db, org_id, tag)
        await db.commit()
    return patient.id, device.id, demo_device_key(tag)


async def test_dedup_gap_backlog_alert_dedupe_and_device_task(client, auth, app, ws_base, fake_pipeline):
    from cardiosense.db import session_factory
    from cardiosense.models import Alert, Beat, Device, SessionGap

    clin = await auth.headers("clinician")
    pid, device_id, key = await _demo_target("mech1")
    token = await auth.token("clinician")
    stream = DeviceStream(ws_base, device_id, key)
    ack = await stream.open(HELLO)
    sid = ack["session_id"]
    assert ack["type"] == "ack"

    # 25 s of signal, with one frame repeated (same seq) and one frame from the past (new seq, old t0)
    for _ in range(25):
        await stream.send_signal(SECOND)
    await stream.frame(SECOND[:36], seq=stream.seq - 1, t0=stream.t0 - 36, advance=False)
    await stream.frame(SECOND[:36], seq=stream.seq, t0=0, advance=False)
    stream.seq += 1

    while app.state.ingest.active_session(pid).next_t0 < 25 * FS:  # let the server catch up with the burst
        await asyncio.sleep(0.02)

    # a UI that joins late gets the session, ~10 s of signal, the recent beats and the latest window
    async with LiveCollector(ws_base, pid, token) as live:
        await live.wait_for(lambda m: m["type"] == "window")
        await asyncio.sleep(0.3)
        kinds = [m["type"] for _, m in live.messages]
        assert kinds[0] == "session" and live.of("session")[0]["active"] is True
        assert live.of("status")[0]["stream"] == "connected"
        backlog_signal = sum(len(m["mv"]) for m in live.of("signal"))
        assert 9 * FS <= backlog_signal <= 11 * FS
        assert len(live.of("beat")) == 24 and live.of("window")[-1]["start_s"] == 10.0
        first_alert = live.of("alert")[-1]["alert"]
        assert first_alert["rule_id"] == "V_RUN" and first_alert["state"] == "open" and first_alert["occurrences"] == 1

        # the same finding again: no new alert row, the open one is updated and re-published
        for _ in range(10):
            await stream.send_signal(SECOND)
        again = await live.wait_for(lambda m: m["type"] == "alert" and m["alert"]["occurrences"] == 2)
        assert again["alert"]["id"] == first_alert["id"]

        # acknowledged alerts are not "open": the next episode raises a new alert
        await app.state.writer.drain()
        acked = await client.post(f"/alerts/{first_alert['id']}/ack", json={"note": "seen"}, headers=clin)
        assert acked.status_code == 200 and acked.json()["state"] == "acknowledged"
        await live.wait_for(lambda m: m["type"] == "alert" and m["alert"]["state"] == "acknowledged")
        for _ in range(10):
            await stream.send_signal(SECOND)
        fresh = await live.wait_for(lambda m: m["type"] == "alert" and m["alert"]["id"] != first_alert["id"])
        assert fresh["alert"]["state"] == "open"

        # a jump in t0 is a gap (recorded, never interpolated); lead-off raises a device task, not an alert
        stream.skip(3 * FS)
        await stream.send_signal(SECOND, lead_off=True)
        await live.wait_for(lambda m: m["type"] == "quality" and m["state"] == "lead_off")
        assert any(m["state"] == "gap" for m in live.of("quality"))
        await app.state.writer.drain()
        p = (await client.get(f"/patients/{pid}", headers=clin)).json()
        assert p["signal_status"] == "lead_off" and p["active_session_id"] == sid and p["device_id"] == device_id
        devices = {d["device_id"]: d for d in (await client.get("/devices", headers=await auth.headers("tech"))).json()}
        assert devices[device_id]["status"] == "streaming" and devices[device_id]["task"]["rule_id"] == "LEAD_OFF"

        await stream.bye()
        await live.wait_for(lambda m: m["type"] == "status" and m["stream"] == "disconnected")

    await app.state.writer.drain()
    async with session_factory()() as db:
        gaps = (await db.execute(select(SessionGap).where(SessionGap.session_id == sid))).scalars().all()
        alerts = (await db.execute(select(Alert).where(Alert.session_id == sid).order_by(Alert.created_at))).scalars().all()
        beats = await db.scalar(select(func.count()).select_from(Beat).where(Beat.session_id == sid))
        device = await db.get(Device, device_id)
    assert [(g.at_sample, g.missing) for g in gaps] == [(45 * FS, 3 * FS)]
    assert [a.state for a in alerts] == ["acknowledged", "open"] and alerts[0].occurrences == 2
    assert all(a.rule_id not in ("LEAD_OFF", "LOW_SQI") for a in alerts)
    assert beats == 49 and device.sqi is not None
    s = (await client.get(f"/sessions/{sid}", headers=clin)).json()
    assert s["summary"]["duplicates_dropped"] == 1 and s["summary"]["out_of_order_dropped"] == 1
    assert s["summary"]["gaps"] == 1 and s["summary"]["missing_samples"] == 3 * FS and s["summary"]["alerts"] == 2
    sig = (await client.get(f"/sessions/{sid}/signal", params={"from": 44, "to": 49}, headers=clin)).json()
    assert sig["missing_samples"] == 3 * FS and sig["mv"][FS:4 * FS] == [None] * (3 * FS) and sig["mv"][0] == 0.0
    events = (await client.get(f"/sessions/{sid}/events", params={"kinds": "gap,alert"}, headers=clin)).json()
    assert [e["kind"] for e in events].count("gap") == 1 and [e["kind"] for e in events].count("alert") == 2
    p = (await client.get(f"/patients/{pid}", headers=clin)).json()
    assert p["signal_status"] == "offline" and p["risk_tier"] == "orange" and p["last_event"]["rule_id"] == "V_RUN"


async def test_new_connection_takes_over_the_device(client, auth, app, ws_base, fake_pipeline):
    clin = await auth.headers("clinician")
    pid, device_id, key = await _demo_target("mech2")
    first = DeviceStream(ws_base, device_id, key)
    a = await first.open(HELLO)
    await first.send_signal(SECOND * 2)
    second = DeviceStream(ws_base, device_id, key)
    b = await second.open(HELLO)
    assert b["type"] == "ack" and b["session_id"] != a["session_id"]
    with pytest.raises(websockets.ConnectionClosed):
        for _ in range(50):
            await first.ws.recv()
    assert first.ws.close_code == 4409
    await second.send_signal(SECOND * 2)  # t0 restarts at 0 in the new session
    await second.bye()
    await app.state.writer.drain()
    sessions = (await client.get(f"/patients/{pid}/sessions", headers=clin)).json()
    by_id = {s["id"]: s for s in sessions}
    assert by_id[a["session_id"]]["ended_at"] and by_id[b["session_id"]]["ended_at"]
    assert by_id[a["session_id"]]["summary"]["duration_s"] == 2.0 and by_id[b["session_id"]]["summary"]["gaps"] == 0
    assert app.state.ingest.active_session(pid) is None


async def test_withdrawing_consent_stops_the_stream(client, auth, app, ws_base, fake_pipeline):
    clin = await auth.headers("clinician")
    pid, device_id, key = await _demo_target("mech3")
    stream = DeviceStream(ws_base, device_id, key)
    assert (await stream.open(HELLO))["type"] == "ack"
    await stream.send_signal(SECOND)
    r = await client.patch(f"/patients/{pid}", json={"consent": False}, headers=clin)
    assert r.status_code == 200 and r.json()["consent"] is False and r.json()["active_session_id"] is None
    with pytest.raises(websockets.ConnectionClosed):
        for _ in range(50):
            await stream.ws.recv()
    assert stream.ws.close_code == 1008
    again = DeviceStream(ws_base, device_id, key)
    refused = await again.open(HELLO)
    assert refused["type"] == "error" and "consent" in refused["detail"]
    await again.close()


async def test_live_socket_requires_a_permitted_user(client, auth, ws_base, demo_patients):
    pid = demo_patients["208"]["id"]
    for token in ("garbage", ""):
        async with websockets.connect(f"{ws_base}/v1/live/{pid}?token={token}") as ws:
            with pytest.raises(websockets.ConnectionClosed):
                await ws.recv()
            assert ws.close_code == 4401
    patient_token = await auth.token("patient")  # linked to the stable demo patient, not to 208
    async with websockets.connect(f"{ws_base}/v1/live/{pid}?token={patient_token}") as ws:
        with pytest.raises(websockets.ConnectionClosed):
            await ws.recv()
        assert ws.close_code == 4403
    async with LiveCollector(ws_base, demo_patients["100"]["id"], patient_token) as live:
        await live.wait_for(lambda m: m["type"] == "status")


async def test_soak_devices_can_be_created(client, auth, ws_base, fake_pipeline):
    admin = await auth.headers("admin")
    assert (await client.post("/demo/soak-devices", json={"count": 3}, headers=await auth.headers("clinician"))).status_code == 403
    r = await client.post("/demo/soak-devices", json={"count": 3}, headers=admin)
    assert r.status_code == 200 and [d["device_id"] for d in r.json()["devices"]] == ["dev-soak-000", "dev-soak-001", "dev-soak-002"]
    again = await client.post("/demo/soak-devices", json={"count": 3}, headers=admin)
    assert again.json()["devices"] == r.json()["devices"]  # idempotent
    stream = DeviceStream(ws_base, "dev-soak-002", "soak-key-002")
    assert (await stream.open(HELLO))["type"] == "ack"
    await stream.bye()
    assert (await client.delete("/demo/soak-devices", headers=admin)).json()["removed"] == 3
