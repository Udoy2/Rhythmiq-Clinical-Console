"""Auth flow (FR-01/02) and server-side RBAC denials (FR-04, BRD §7.3)."""

from __future__ import annotations

import uuid

import pyotp
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

PASSWORD = "S3cure-enough-pass"


def _email(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"


def _bearer(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_login_me_and_bad_credentials(client):
    r = await client.post("/auth/login", json={"email": "clinician@cardiosense.dev", "password": "CardioSense!23"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer" and body["access_token"] and body["refresh_token"]
    assert body["user"]["role"] == "clinician" and body["user"]["mfa_enabled"] is False
    assert set(body["user"]) >= {"id", "email", "name", "role", "org_id", "mfa_enabled"}

    me = await client.get("/auth/me", headers=_bearer(body))
    assert me.status_code == 200 and me.json()["email"] == "clinician@cardiosense.dev"

    assert (await client.post("/auth/login", json={"email": "clinician@cardiosense.dev", "password": "nope-nope-nope"})).status_code == 401
    assert (await client.get("/auth/me")).status_code == 401
    assert (await client.get("/auth/me", headers={"Authorization": "Bearer garbage"})).status_code == 401


async def test_passwords_are_argon2id(app):
    from sqlalchemy import select

    from cardiosense.db import session_factory
    from cardiosense.models import User

    async with session_factory()() as db:
        hashed = (await db.execute(select(User.password_hash).where(User.email == "clinician@cardiosense.dev"))).scalar_one()
    assert hashed.startswith("$argon2id$")


async def test_refresh_rotates_and_reuse_revokes_the_session(client):
    r = await client.post("/auth/register", json={"email": _email("rot"), "password": PASSWORD, "name": "Rot Ation"})
    assert r.status_code == 201, r.text
    first = r.json()

    second = await client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert second.status_code == 200, second.text
    second = second.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert (await client.get("/auth/me", headers=_bearer(second))).status_code == 200

    # Re-using the rotated (old) token is treated as theft: the whole session is revoked.
    assert (await client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})).status_code == 401
    assert (await client.post("/auth/refresh", json={"refresh_token": second["refresh_token"]})).status_code == 401
    assert (await client.get("/auth/me", headers=_bearer(second))).status_code == 401
    assert (await client.post("/auth/refresh", json={"refresh_token": "not-a-token"})).status_code == 401


async def test_logout_revokes_access_and_refresh_tokens(client):
    r = await client.post("/auth/register", json={"email": _email("out"), "password": PASSWORD, "name": "Log Out"})
    tokens = r.json()
    assert (await client.get("/auth/me", headers=_bearer(tokens))).status_code == 200
    out = await client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]}, headers=_bearer(tokens))
    assert out.status_code == 200
    assert (await client.get("/auth/me", headers=_bearer(tokens))).status_code == 401
    assert (await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})).status_code == 401


async def test_register_validation_and_duplicates(client):
    email = _email("dup")
    assert (await client.post("/auth/register", json={"email": email, "password": "short", "name": "X"})).status_code == 422
    assert (await client.post("/auth/register", json={"email": email, "password": PASSWORD, "name": "X"})).status_code == 201
    assert (await client.post("/auth/register", json={"email": email, "password": PASSWORD, "name": "X"})).status_code == 409


async def test_mfa_enrol_verify_login_and_recovery_code(client):
    email = _email("mfa")
    tokens = (await client.post("/auth/register", json={"email": email, "password": PASSWORD, "name": "M Fa"})).json()
    enrol = await client.post("/auth/mfa/enrol", headers=_bearer(tokens))
    assert enrol.status_code == 200, enrol.text
    secret = enrol.json()["secret"]
    assert enrol.json()["otpauth_url"].startswith("otpauth://totp/")
    assert (await client.post("/auth/mfa/verify", json={"otp": "000000"}, headers=_bearer(tokens))).status_code == 400
    verify = await client.post("/auth/mfa/verify", json={"otp": pyotp.TOTP(secret).now()}, headers=_bearer(tokens))
    assert verify.status_code == 200 and verify.json()["mfa_enabled"] is True
    recovery = verify.json()["recovery_codes"]
    assert len(recovery) == 8

    creds = {"email": email, "password": PASSWORD}
    need = await client.post("/auth/login", json=creds)
    assert need.status_code == 401 and need.json().get("mfa_required") is True
    assert (await client.post("/auth/login", json={**creds, "otp": "123456"})).status_code == 401
    ok = await client.post("/auth/login", json={**creds, "otp": pyotp.TOTP(secret).now()})
    assert ok.status_code == 200 and ok.json()["user"]["mfa_enabled"] is True
    assert (await client.post("/auth/login", json={**creds, "otp": recovery[0]})).status_code == 200
    assert (await client.post("/auth/login", json={**creds, "otp": recovery[0]})).status_code == 401  # single use


async def test_patient_account_sees_only_itself(client, auth, demo_patients):
    tokens = (await client.post("/auth/register", json={"email": _email("pat"), "password": PASSWORD, "name": "Pat Ient"})).json()
    h = _bearer(tokens)
    assert tokens["user"]["role"] == "patient"
    mine = await client.get("/patients", headers=h)
    assert mine.status_code == 200 and len(mine.json()) == 1
    own_id = mine.json()[0]["id"]
    assert tokens["user"]["patient_id"] == own_id and mine.json()[0]["consent"] is False

    other = demo_patients["208"]["id"]
    for path in (f"/patients/{other}", f"/patients/{other}/notes", f"/patients/{other}/sessions",
                 f"/patients/{other}/trends", f"/reports/{other}.pdf"):
        assert (await client.get(path, headers=h)).status_code == 403, path
    assert (await client.get("/alerts", params={"patient_id": other}, headers=h)).status_code == 403
    assert (await client.get("/alerts", headers=h)).json() == []
    # things a patient may never do
    assert (await client.post("/patients", json={"name": "Nope", "consent": True}, headers=h)).status_code == 403
    assert (await client.post("/copilot/query", json={"patient_id": own_id}, headers=h)).status_code == 403
    assert (await client.get("/admin/users", headers=h)).status_code == 403
    assert (await client.post("/demo/replay", json={"record": "100", "dataset": "MIT-BIH", "speed": 1}, headers=h)).status_code == 403
    # may give consent, but not edit the clinical profile
    assert (await client.patch(f"/patients/{own_id}", json={"name": "Hacked"}, headers=h)).status_code == 403
    consent = await client.patch(f"/patients/{own_id}", json={"consent": True}, headers=h)
    assert consent.status_code == 200 and consent.json()["consent"] is True

    # the seeded demo patient account is linked to the stable demo patient only
    seeded = await client.get("/patients", headers=await auth.headers("patient"))
    assert [p["id"] for p in seeded.json()] == [demo_patients["100"]["id"]]


async def test_organisations_are_isolated(client, demo_patients):
    r = await client.post("/auth/register", json={"email": _email("org"), "password": PASSWORD, "name": "Other Admin",
                                                  "org_name": f"Other Clinic {uuid.uuid4().hex[:6]}"})
    assert r.status_code == 201 and r.json()["user"]["role"] == "org_admin"
    h = _bearer(r.json())
    assert (await client.get("/patients", headers=h)).json() == []
    pid = demo_patients["208"]["id"]
    assert (await client.get(f"/patients/{pid}", headers=h)).status_code == 404
    assert (await client.get("/alerts", headers=h)).json() == []
    assert (await client.get("/alerts", params={"patient_id": pid}, headers=h)).status_code == 404
    assert [u["email"] for u in (await client.get("/admin/users", headers=h)).json()] == [r.json()["user"]["email"]]
    assert all(a["actor_email"] == r.json()["user"]["email"] for a in (await client.get("/audit", headers=h)).json())


async def test_role_matrix_denials(client, auth, demo_patients):
    tech, clin, admin = [await auth.headers(w) for w in ("tech", "clinician", "admin")]
    pid = demo_patients["223"]["id"]
    alerts = (await client.get("/alerts", params={"patient_id": pid}, headers=tech)).json()
    assert alerts, "seeded alerts expected"
    # technician: sees patients and alerts, but cannot work alerts, annotate, use the copilot or administer
    assert (await client.post(f"/alerts/{alerts[0]['id']}/ack", json={"note": "x"}, headers=tech)).status_code == 403
    assert (await client.post(f"/patients/{pid}/notes", json={"text": "x"}, headers=tech)).status_code == 403
    assert (await client.post("/copilot/query", json={"patient_id": pid}, headers=tech)).status_code == 403
    assert (await client.get("/admin/users", headers=tech)).status_code == 403
    assert (await client.get("/audit", headers=tech)).status_code == 403
    assert (await client.get("/admin/rules", headers=tech)).status_code == 403
    # clinician: may read rules ("suggest") but not change them; no user administration; audit = own actions
    assert (await client.get("/admin/rules", headers=clin)).status_code == 200
    assert (await client.put("/admin/rules", json={"thresholds": {}}, headers=clin)).status_code == 403
    assert (await client.get("/admin/users", headers=clin)).status_code == 403
    assert (await client.post("/devices", json={"name": "x"}, headers=clin)).status_code == 403
    own = (await client.get("/audit", headers=clin)).json()
    assert own and all(a["actor_email"] == "clinician@cardiosense.dev" for a in own)
    # org admin: everything in the organisation
    assert (await client.get("/admin/users", headers=admin)).status_code == 200
    assert (await client.get("/audit", headers=admin)).status_code == 200


async def test_logins_are_audited(client, auth):
    await client.post("/auth/login", json={"email": "tech@cardiosense.dev", "password": "wrong-password-1"})
    await client.post("/auth/login", json={"email": "tech@cardiosense.dev", "password": "CardioSense!23"})
    rows = (await client.get("/audit", params={"actor": "tech@cardiosense.dev", "action": "auth.login"},
                             headers=await auth.headers("admin"))).json()
    actions = {r["action"] for r in rows}
    assert {"auth.login", "auth.login_failed"} <= actions
