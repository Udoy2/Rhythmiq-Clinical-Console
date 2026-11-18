"""Shared fixtures: the real app on the ``cardiosense_test`` PostgreSQL database, an in-process HTTP client,
and a real uvicorn server on a free port for WebSocket tests (same event loop, same app state)."""

from __future__ import annotations

import asyncio
import os
import socket
import sys
from pathlib import Path


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = _free_port()
BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Must be set before cardiosense.config is imported.
os.environ["CARDIOSENSE_DATABASE_URL"] = os.environ.get(
    "CARDIOSENSE_TEST_DATABASE_URL", "postgresql+asyncpg://cardiosense:cardiosense@localhost:5432/cardiosense_test"
)
os.environ["CARDIOSENSE_SEED"] = "1"
os.environ["CARDIOSENSE_SEED_HISTORY_DAYS"] = "4"
os.environ["CARDIOSENSE_LLM_ENABLED"] = "0"  # tests never call a hosted model; they inject fake generators
os.environ["CARDIOSENSE_SELF_WS_URL"] = f"ws://127.0.0.1:{PORT}"
os.environ["CARDIOSENSE_STREAM_IDLE_TIMEOUT_S"] = "15"
os.environ.pop("CARDIOSENSE_SMTP_HOST", None)
os.environ.pop("CARDIOSENSE_ALERT_WEBHOOK_URL", None)

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
import uvicorn  # noqa: E402
from sqlalchemy import text  # noqa: E402

SEED_PASSWORD = "CardioSense!23"
EMAILS = {
    "admin": "admin@cardiosense.dev",
    "clinician": "clinician@cardiosense.dev",
    "tech": "tech@cardiosense.dev",
    "patient": "patient@cardiosense.dev",
}


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def app():
    from cardiosense.db import get_engine
    from cardiosense.main import app as application
    from cardiosense.services.pipeline import PipelineProvider

    assert "cardiosense_test" in os.environ["CARDIOSENSE_DATABASE_URL"], "refusing to wipe a non-test database"
    async with get_engine().begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))

    try:
        import cardiosense.core  # noqa: F401
    except Exception:  # real core not importable: run the whole suite on the stand-in with the same interface
        import fake_core

        provider = PipelineProvider()
        provider.install(fake_core.FakeStreamProcessor, rule_version=fake_core.RULE_VERSION,
                         model_version=fake_core.MODEL_VERSION)
        application.state.pipeline = provider

    async with application.router.lifespan_context(application):
        config = uvicorn.Config(application, host="127.0.0.1", port=PORT, log_level="warning", lifespan="off")
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        task = asyncio.create_task(server.serve())
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.05)
        assert server.started, "uvicorn did not start"
        try:
            yield application
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=20)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver/v1", timeout=60) as c:
        yield c


@pytest.fixture(scope="session")
def ws_base() -> str:
    return f"ws://127.0.0.1:{PORT}"


class Auth:
    """Logs seeded (or given) users in once and hands out bearer headers."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self._tokens: dict[str, dict] = {}

    async def login(self, who: str, password: str = SEED_PASSWORD) -> dict:
        email = EMAILS.get(who, who)
        if email not in self._tokens:
            r = await self.client.post("/auth/login", json={"email": email, "password": password})
            assert r.status_code == 200, r.text
            self._tokens[email] = r.json()
        return self._tokens[email]

    async def headers(self, who: str, password: str = SEED_PASSWORD) -> dict[str, str]:
        return {"Authorization": f"Bearer {(await self.login(who, password))['access_token']}"}

    async def token(self, who: str) -> str:
        return (await self.login(who))["access_token"]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def auth(client) -> Auth:
    return Auth(client)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def demo_patients(client, auth) -> dict[str, dict]:
    """Seeded demo patients by record id: {"100": {...}, "223": {...}, "208": {...}}."""
    r = await client.get("/patients", headers=await auth.headers("clinician"))
    assert r.status_code == 200, r.text
    return {p["demo_record"]: p for p in r.json() if p["is_demo"] and p["demo_record"] in ("100", "223", "208")}
