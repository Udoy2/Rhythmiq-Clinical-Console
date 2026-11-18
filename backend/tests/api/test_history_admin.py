"""Trends, sessions of the seeded demo history, exports, meta/health and the admin console."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_trends_have_buckets_and_gaps(client, auth, demo_patients):
    clin = await auth.headers("clinician")
    pid = demo_patients["208"]["id"]
    r = await client.get(f"/patients/{pid}/trends", params={"range": "7d"}, headers=clin)
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["range"] == "7d" and t["bucket_s"] == 3600 and t["buckets"] and t["gaps"]
    b = t["buckets"][0]
    assert set(b) >= {"t", "hr_bpm", "sdnn_ms", "rmssd_ms", "s_burden_pct", "v_burden_pct", "risk_score", "findings"}
    assert b["findings"] and set(b["findings"][0]) == {"rule_id", "label", "severity", "count"}
    assert [x["t"] for x in t["buckets"]] == sorted(x["t"] for x in t["buckets"])
    # gaps and buckets tile the range: no bucket lies inside a gap
    for g in t["gaps"]:
        assert g["start"] < g["end"] and not any(g["start"] <= x["t"] < g["end"] for x in t["buckets"])
    assert t["summary"]["v_burden_pct"] > 10 and t["summary"]["risk_score_max"] >= 50
    stable = (await client.get(f"/patients/{demo_patients['100']['id']}/trends", params={"range": "30d"}, headers=clin)).json()
    assert stable["bucket_s"] == 21600 and stable["summary"]["v_burden_pct"] < 1
    for rng in ("24h", "90d"):
        assert (await client.get(f"/patients/{pid}/trends", params={"range": rng}, headers=clin)).status_code == 200
    assert (await client.get(f"/patients/{pid}/trends", params={"range": "1y"}, headers=clin)).status_code == 422


async def test_seeded_sessions_and_events(client, auth, demo_patients):
    clin = await auth.headers("clinician")
    pid = demo_patients["223"]["id"]
    sessions = (await client.get(f"/patients/{pid}/sessions", headers=clin)).json()
    seeded = [s for s in sessions if s["source"] == "seed"]
    assert len(seeded) >= 6 and all(s["is_demo"] and s["summary"]["seeded_history"] for s in seeded)
    assert [s["started_at"] for s in sessions] == sorted((s["started_at"] for s in sessions), reverse=True)
    sid = seeded[0]["id"]
    one = (await client.get(f"/sessions/{sid}", headers=clin)).json()
    assert one["id"] == sid and one["rule_version"] and one["active"] is False and one["summary"]["windows"] > 0
    events = (await client.get(f"/sessions/{sid}/events", params={"findings_only": "true"}, headers=clin)).json()
    assert events and all(e["kind"] != "window" or e["findings"] for e in events)
    assert (await client.get(f"/sessions/{sid}/beats", headers=clin)).json() == []  # aggregates only
    assert (await client.get(f"/sessions/{sid}/signal", headers=clin)).json()["mv"] == []
    assert (await client.get(f"/sessions/{sid}/signal", params={"from": 0, "to": 900}, headers=clin)).status_code == 422
    csv = await client.get(f"/sessions/{sid}/export.csv", params={"table": "windows"}, headers=clin)
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert "not a medical device" in csv.text and "rule_version=" in csv.text and "model_version=" in csv.text
    assert csv.text.count("\n") > one["summary"]["windows"]
    assert (await client.get("/sessions/00000000-0000-0000-0000-000000000000", headers=clin)).status_code == 404


async def test_pdf_report(client, auth, demo_patients):
    clin, admin = await auth.headers("clinician"), await auth.headers("admin")
    pid = demo_patients["208"]["id"]
    r = await client.get(f"/reports/{pid}.pdf", params={"range": "30d"}, headers=clin)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF") and len(r.content) > 4000
    from io import BytesIO

    try:
        from pypdf import PdfReader
    except ImportError:
        PdfReader = None
    if PdfReader is not None:
        text = " ".join(page.extract_text() for page in PdfReader(BytesIO(r.content)).pages)
        for needle in ("not a medical device", "Model and rule versions", "rules-", "Trend summary", "Alerts",
                       "V_RUN", "DEMO / REPLAY"):
            assert needle in text, needle
    rows = (await client.get("/audit", params={"action": "report.export_pdf", "target_id": pid}, headers=admin)).json()
    assert rows and rows[0]["detail"]["range"] == "30d"
    # the patient account can download its own report
    own = await client.get(f"/reports/{demo_patients['100']['id']}.pdf", headers=await auth.headers("patient"))
    assert own.status_code == 200 and own.content.startswith(b"%PDF")


async def test_meta_and_health(client, app):
    meta = (await client.get("/meta")).json()
    assert meta["disclaimer"] == "Research and demonstration system — not a medical device. Not an emergency service."
    assert meta["rule_version"].startswith("rules-") and isinstance(meta["model_loaded"], bool)
    assert meta["model_loaded"] == app.state.pipeline.model_loaded and meta["limitations"]
    if meta["model_loaded"]:
        assert meta["model_version"] and meta["model"]["model_version"] == meta["model_version"]
    else:
        assert meta["model_version"] is None and meta["mode"] == "rules-only"
    health = (await client.get("/health")).json()
    assert health["status"] == "ok" and health["db"] == "ok" and "latency_ms" in health
    assert {"queue_depth", "rows_written", "failed_ops"} <= set(health["writer"])
    assert (await client.get("/metrics")).status_code == 200


async def test_rule_thresholds_are_versioned_and_audited(client, auth):
    admin = await auth.headers("admin")
    before = (await client.get("/admin/rules", headers=admin)).json()
    assert before["thresholds"] == before["config"] and before["rule_version"].startswith("rules-")
    key = "tachy_bpm" if "tachy_bpm" in before["thresholds"] else next(
        k for k, v in before["thresholds"].items() if isinstance(v, float))
    new_value = before["thresholds"][key] + 5
    r = await client.put("/admin/rules", json={"thresholds": {key: new_value}, "note": "test"}, headers=admin)
    assert r.status_code == 200, r.text
    after = r.json()
    assert after["version"] == before["version"] + 1 and after["thresholds"][key] == new_value
    assert after["updated_by"] == "admin@cardiosense.dev" and after["history"][0]["version"] == after["version"]
    same = (await client.put("/admin/rules", json={"thresholds": {key: new_value}}, headers=admin)).json()
    assert same["version"] == after["version"]  # no change, no new version
    assert (await client.put("/admin/rules", json={"thresholds": {"no_such_rule": 1}}, headers=admin)).status_code == 422
    assert (await client.put("/admin/rules", json={"thresholds": {key: "fast"}}, headers=admin)).status_code == 422
    assert (await client.put("/admin/rules", json={"thresholds": {key: -1}}, headers=admin)).status_code == 422
    rows = (await client.get("/audit", params={"action": "admin.rules_update"}, headers=admin)).json()
    assert rows[0]["detail"]["changed"][key] == {"from": before["thresholds"][key], "to": new_value}
    # restore the defaults for the rest of the suite (stored as a further version)
    restored = await client.put("/admin/rules", json={"thresholds": {key: before["thresholds"][key]}}, headers=admin)
    assert restored.json()["version"] == after["version"] + 1


async def test_admin_users(client, auth):
    admin = await auth.headers("admin")
    users = (await client.get("/admin/users", headers=admin)).json()
    assert {"admin@cardiosense.dev", "clinician@cardiosense.dev", "tech@cardiosense.dev", "patient@cardiosense.dev"} <= {
        u["email"] for u in users}
    created = await client.post("/admin/users", headers=admin, json={
        "email": "nurse@example.com", "name": "Night Nurse", "role": "technician", "password": "Initial-pass-123"})
    assert created.status_code == 201, created.text
    uid = created.json()["id"]
    login = await client.post("/auth/login", json={"email": "nurse@example.com", "password": "Initial-pass-123"})
    tokens = login.json()
    h = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert (await client.post("/copilot/query", json={"patient_id": uid}, headers=h)).status_code == 403

    promoted = await client.patch(f"/admin/users/{uid}", json={"role": "clinician"}, headers=admin)
    assert promoted.status_code == 200 and promoted.json()["role"] == "clinician"
    assert (await client.get("/auth/me", headers=h)).json()["role"] == "clinician"  # roles are read server-side
    assert (await client.patch(f"/admin/users/{uid}", json={"role": "system_admin"}, headers=admin)).status_code == 403

    off = await client.patch(f"/admin/users/{uid}", json={"active": False}, headers=admin)
    assert off.status_code == 200 and off.json()["active"] is False
    assert (await client.get("/auth/me", headers=h)).status_code == 401
    assert (await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})).status_code == 401
    assert (await client.post("/auth/login", json={"email": "nurse@example.com", "password": "Initial-pass-123"})).status_code == 401

    me = next(u for u in users if u["email"] == "admin@cardiosense.dev")
    assert (await client.patch(f"/admin/users/{me['id']}", json={"active": False}, headers=admin)).status_code == 400
    rows = (await client.get("/audit", params={"action": "admin.user", "target_id": uid}, headers=admin)).json()
    assert {"admin.user_create", "admin.user_update"} <= {r["action"] for r in rows}
