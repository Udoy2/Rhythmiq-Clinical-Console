"""Alert queue and workflow with audit (FR-15, FR-20): acknowledge, escalate, close; never auto-closed."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

RANK = {"red": 0, "orange": 1, "amber": 2, "green": 3}


async def test_queue_is_red_first_and_filterable(client, auth, demo_patients):
    clin = await auth.headers("clinician")
    alerts = (await client.get("/alerts", headers=clin)).json()
    assert len(alerts) >= 6
    tiers = [RANK[a["tier"]] for a in alerts]
    assert tiers == sorted(tiers)
    a = alerts[0]
    assert set(a) >= {"id", "patient_id", "session_id", "rule_id", "tier", "title", "detail", "state", "created_at",
                      "updated_at", "acknowledged_by", "note", "evidence", "patient_name"}
    assert {"start_s", "end_s", "fs", "strip_mv", "beats", "rule_version", "model_version"} <= set(a["evidence"])
    assert all(x["is_demo"] for x in alerts if x["patient_id"] == demo_patients["208"]["id"])

    pid = demo_patients["208"]["id"]
    only = (await client.get("/alerts", params={"patient_id": pid, "state": "open"}, headers=clin)).json()
    assert only and all(x["patient_id"] == pid and x["state"] == "open" for x in only)
    amber = (await client.get("/alerts", params={"tier": "amber", "evidence": "false"}, headers=clin)).json()
    assert amber and all(x["tier"] == "amber" and "evidence" not in x for x in amber)
    assert (await client.get("/alerts", params={"state": "bogus"}, headers=clin)).status_code == 422
    # seeded evidence strips come from the public recording
    seeded = next(x for x in only if x["evidence"].get("seeded"))
    assert len(seeded["evidence"]["strip_mv"]) == 3600 and seeded["evidence"]["beats"]


async def test_workflow_transitions_are_audited(client, auth, demo_patients):
    clin, admin = await auth.headers("clinician"), await auth.headers("admin")
    pid = demo_patients["223"]["id"]
    alert = (await client.get("/alerts", params={"patient_id": pid, "state": "open"}, headers=clin)).json()[0]
    aid = alert["id"]

    ack = await client.post(f"/alerts/{aid}/ack", json={"note": "Reviewed the strip."}, headers=clin)
    assert ack.status_code == 200, ack.text
    body = ack.json()
    assert body["state"] == "acknowledged" and body["acknowledged_by"] and body["note"] == "Reviewed the strip."
    assert body["acknowledged_at"] and body["updated_at"] >= alert["updated_at"]
    assert (await client.post(f"/alerts/{aid}/ack", json={}, headers=clin)).status_code == 409  # already acknowledged

    esc = await client.post(f"/alerts/{aid}/escalate", json={"note": "Second opinion please."}, headers=admin)
    assert esc.status_code == 200 and esc.json()["state"] == "escalated"
    closed = await client.post(f"/alerts/{aid}/close", headers=clin)  # body is optional
    assert closed.status_code == 200 and closed.json()["state"] == "closed"
    assert closed.json()["note"] == "Second opinion please."
    for action in ("ack", "escalate", "close"):
        assert (await client.post(f"/alerts/{aid}/{action}", json={}, headers=clin)).status_code == 409

    got = (await client.get(f"/alerts/{aid}", headers=clin)).json()
    assert [h["state"] for h in got["history"]] == ["acknowledged", "escalated", "closed"]
    assert [h["actor_email"] for h in got["history"]] == ["clinician@cardiosense.dev", "admin@cardiosense.dev",
                                                         "clinician@cardiosense.dev"]

    rows = (await client.get("/audit", params={"target_id": aid, "action": "alert."}, headers=admin)).json()
    transitions = {r["action"]: r for r in rows}
    assert {"alert.ack", "alert.escalate", "alert.close"} <= set(transitions)
    assert transitions["alert.ack"]["detail"]["from"] == "open" and transitions["alert.ack"]["detail"]["to"] == "acknowledged"
    assert transitions["alert.escalate"]["actor_email"] == "admin@cardiosense.dev"
    assert transitions["alert.close"]["detail"]["rule_id"] == alert["rule_id"]

    assert (await client.post("/alerts/00000000-0000-0000-0000-000000000000/ack", json={}, headers=clin)).status_code == 404
    assert (await client.post("/alerts/nonsense/ack", json={}, headers=clin)).status_code == 404


async def test_patients_and_technicians_cannot_work_alerts(client, auth, demo_patients):
    clin = await auth.headers("clinician")
    alert = (await client.get("/alerts", params={"patient_id": demo_patients["208"]["id"], "state": "open"}, headers=clin)).json()[0]
    for who in ("patient", "tech"):
        r = await client.post(f"/alerts/{alert['id']}/close", json={"note": "no"}, headers=await auth.headers(who))
        assert r.status_code == 403
    assert (await client.get(f"/alerts/{alert['id']}", headers=await auth.headers("patient"))).status_code == 403
    assert (await client.get(f"/alerts/{alert['id']}", headers=clin)).json()["state"] == "open"  # untouched
