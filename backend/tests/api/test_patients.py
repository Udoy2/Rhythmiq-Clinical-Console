"""Patient CRUD, notes, device pairing and the consent gate on the ingest WebSocket (FR-05, FR-06)."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
import websockets

pytestmark = pytest.mark.asyncio(loop_scope="session")

HELLO = {"type": "hello", "fs": 250, "firmware": "test-1.0", "source": "serial",
         "adc": {"zero": 512, "gain": 225.0, "units": "adu"}}


async def _hello(ws_base: str, device_id: str, key: str, hello: dict | None = None) -> dict:
    """Open the ingest socket, send hello, return the server's first reply."""
    async with websockets.connect(f"{ws_base}/v1/stream/{device_id}?key={key}") as ws:
        await ws.send(json.dumps({**(hello or HELLO), "device_id": device_id}))
        reply = json.loads(await ws.recv())
        if reply["type"] == "ack":
            await ws.send(json.dumps({"type": "bye"}))
        return reply


async def test_patient_crud_and_audit(client, auth):
    tech, clin, admin = [await auth.headers(w) for w in ("tech", "clinician", "admin")]
    r = await client.post("/patients", headers=tech, json={
        "name": "Test Person", "mrn": "MRN-0042", "dob": "1970-05-06", "sex": "F", "notes": "palpitations", "consent": False})
    assert r.status_code == 201, r.text
    p = r.json()
    assert set(p) >= {"id", "name", "mrn", "dob", "sex", "notes", "consent", "is_demo", "risk_score", "risk_tier",
                      "last_event", "signal_status", "active_session_id", "device_id", "open_alerts"}
    assert p["consent"] is False and p["is_demo"] is False and p["signal_status"] == "offline"
    assert p["active_session_id"] is None and p["device_id"] is None and p["open_alerts"] == 0

    got = await client.get(f"/patients/{p['id']}", headers=clin)
    assert got.status_code == 200 and got.json()["mrn"] == "MRN-0042"
    assert (await client.get("/patients/not-a-uuid", headers=clin)).status_code == 404
    assert (await client.post("/patients", headers=tech, json={"name": ""})).status_code == 422

    upd = await client.patch(f"/patients/{p['id']}", headers=clin, json={"notes": "updated", "sex": "F"})
    assert upd.status_code == 200 and upd.json()["notes"] == "updated"

    found = await client.get("/patients", params={"q": "MRN-0042"}, headers=clin)
    assert [x["id"] for x in found.json()] == [p["id"]]

    rows = (await client.get("/audit", params={"target_id": p["id"]}, headers=admin)).json()
    actions = [r["action"] for r in rows]
    assert "patient.create" in actions and "patient.update" in actions and "patient.view" in actions
    update = next(r for r in rows if r["action"] == "patient.update")
    assert update["detail"]["fields"] == ["notes"] and update["actor_email"] == "clinician@cardiosense.dev"
    assert "updated" not in json.dumps(rows)  # clinical content is not copied into the audit trail


async def test_patient_list_is_sorted_by_risk(client, auth, demo_patients):
    patients = (await client.get("/patients", headers=await auth.headers("clinician"))).json()
    rank = {"green": 0, "amber": 1, "orange": 2, "red": 3}
    keys = [(rank[p["risk_tier"]], p["risk_score"]) for p in patients]
    assert keys == sorted(keys, reverse=True)
    assert demo_patients["208"]["risk_tier"] in ("orange", "red") and demo_patients["100"]["risk_tier"] == "green"
    assert demo_patients["208"]["device_id"] == "dev-demo-208" and demo_patients["208"]["open_alerts"] >= 1
    only_demo = (await client.get("/patients", params={"demo": "false"}, headers=await auth.headers("clinician"))).json()
    assert all(not p["is_demo"] for p in only_demo)


async def test_notes(client, auth, demo_patients):
    pid = demo_patients["100"]["id"]
    clin = await auth.headers("clinician")
    r = await client.post(f"/patients/{pid}/notes", headers=clin, json={"text": "Reviewed strip, no action.", "kind": "note"})
    assert r.status_code == 201 and r.json()["author_name"] and r.json()["kind"] == "note"
    # the patient account (linked to the stable demo patient) can report a symptom on its own record
    s = await client.post(f"/patients/{pid}/notes", headers=await auth.headers("patient"), json={"text": "Felt a flutter."})
    assert s.status_code == 201 and s.json()["kind"] == "symptom"
    notes = (await client.get(f"/patients/{pid}/notes", headers=clin)).json()
    assert [n["text"] for n in notes[:2]] == ["Felt a flutter.", "Reviewed strip, no action."]


async def test_pairing_and_consent_gate(client, auth, ws_base, app):
    tech = await auth.headers("tech")
    patient = (await client.post("/patients", headers=tech, json={"name": "Consent Gate", "consent": False})).json()

    dev = await client.post("/devices", headers=tech, json={"patient_id": patient["id"], "name": "Kit 7"})
    assert dev.status_code == 201, dev.text
    device = dev.json()
    assert device["pairing_code"] and device["paired"] is False and device["status"] == "pairing"

    assert (await client.post("/devices/pair", headers=tech, json={"pairing_code": "ZZZZZZ"})).status_code == 404
    pair = await client.post("/devices/pair", headers=tech, json={"pairing_code": device["pairing_code"]})
    assert pair.status_code == 200, pair.text
    device_id, key = pair.json()["device_id"], pair.json()["device_key"]
    assert device_id == device["device_id"] and len(key) >= 32
    # the code is single use, and the key is stored only as a hash
    assert (await client.post("/devices/pair", headers=tech, json={"pairing_code": device["pairing_code"]})).status_code == 404
    listed = next(d for d in (await client.get("/devices", headers=tech)).json() if d["device_id"] == device_id)
    assert listed["paired"] is True and "device_key" not in listed and listed["patient_name"] == "Consent Gate"

    # wrong key -> rejected; right key but no consent -> rejected
    bad = await _hello(ws_base, device_id, "wrong-key")
    assert bad["type"] == "error" and "key" in bad["detail"]
    gated = await _hello(ws_base, device_id, key)
    assert gated["type"] == "error" and "consent" in gated["detail"]
    assert (await client.get(f"/patients/{patient['id']}/sessions", headers=tech)).json() == []

    # consent recorded -> stream accepted and a session exists
    ok = await client.patch(f"/patients/{patient['id']}", headers=tech, json={"consent": True})
    assert ok.json()["consent"] is True and ok.json()["consent_at"]
    ack = await _hello(ws_base, device_id, key)
    assert ack["type"] == "ack" and ack["session_id"] and isinstance(ack["model_loaded"], bool)
    await app.state.writer.drain()
    sessions = (await client.get(f"/patients/{patient['id']}/sessions", headers=tech)).json()
    assert [s["id"] for s in sessions] == [ack["session_id"]] and sessions[0]["source"] == "serial"

    # a real patient's device may not send ground-truth replays (demo data is never mixed in)
    replay = await _hello(ws_base, device_id, key, {**HELLO, "source": "replay", "record": "208"})
    assert replay["type"] == "error" and "demo" in replay["detail"]


async def test_pairing_code_expires(client, auth, app):
    from cardiosense.db import session_factory, utcnow
    from cardiosense.models import Device

    tech = await auth.headers("tech")
    patient = (await client.post("/patients", headers=tech, json={"name": "Expiry", "consent": True})).json()
    device = (await client.post("/devices", headers=tech, json={"patient_id": patient["id"]})).json()
    async with session_factory()() as db:
        row = await db.get(Device, device["device_id"])
        assert timedelta(minutes=9) < row.pairing_expires_at - utcnow() <= timedelta(minutes=10)
        row.pairing_expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()
    r = await client.post("/devices/pair", headers=tech, json={"pairing_code": device["pairing_code"]})
    assert r.status_code == 400 and "expired" in r.json()["detail"]
