"""Alert notifications (FR-16): in-app always, e-mail and webhook as logged delivery attempts with retry.

Runs entirely off the live path. Payloads carry no patient identifiers and no ECG: only the alert id,
tier and rule, so the recipient opens the console to see the evidence.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage
from typing import Any

import httpx
from sqlalchemy import select, update

from cardiosense.config import DISCLAIMER, Settings, get_settings
from cardiosense.db import new_id, session_factory, utcnow
from cardiosense.models import AlertDelivery, User

log = logging.getLogger("cardiosense.notify")

EMAIL_TIERS = ("orange", "red")


class Notifier:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()
        self.sent = 0
        self.failed = 0

    def alert_created(self, alert: dict[str, Any], org_id: str) -> None:
        """Schedule notifications for a new alert; returns immediately."""
        summary = {k: alert[k] for k in ("id", "tier", "rule_id", "title", "created_at")}
        summary["is_demo"] = bool(alert.get("is_demo"))
        task = asyncio.create_task(self._deliver(summary, org_id), name=f"notify-{alert['id'][:8]}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def shutdown(self) -> None:
        for task in tuple(self._tasks):
            task.cancel()
        await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    def stats(self) -> dict[str, int]:
        return {"pending": len(self._tasks), "sent": self.sent, "failed": self.failed}

    # ------------------------------------------------------------ delivery

    async def _deliver(self, alert: dict[str, Any], org_id: str) -> None:
        settings = get_settings()
        try:
            await self._record(alert["id"], "in_app", "console", "delivered", 1)
            jobs = []
            if alert["tier"] in EMAIL_TIERS:
                for email in await self._recipients(org_id):
                    jobs.append(self._attempt(alert, "email", email, settings))
            if settings.alert_webhook_url:
                jobs.append(self._attempt(alert, "webhook", settings.alert_webhook_url, settings))
            if jobs:
                await asyncio.gather(*jobs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - notification problems must never surface in the live path
            self.failed += 1
            log.error("notification pipeline error for alert %s: %s", alert["id"], type(exc).__name__)

    @staticmethod
    async def _recipients(org_id: str) -> list[str]:
        async with session_factory()() as db:
            rows = await db.execute(
                select(User.email).where(
                    User.org_id == org_id, User.is_active.is_(True), User.role.in_(("clinician", "org_admin"))
                )
            )
            return [r[0] for r in rows]

    async def _attempt(self, alert: dict[str, Any], channel: str, target: str, settings: Settings) -> None:
        if channel == "email" and not settings.smtp_host:
            # No SMTP configured: the attempt is recorded and logged instead of sent.
            log.info("alert %s (%s/%s): e-mail notification logged (SMTP not configured)",
                     alert["id"], alert["tier"], alert["rule_id"])
            await self._record(alert["id"], channel, target, "logged", 1)
            return
        delivery_id = await self._record(alert["id"], channel, target, "retrying", 0)
        error: str | None = None
        for attempt in range(1, settings.notify_max_attempts + 1):
            try:
                if channel == "email":
                    await asyncio.to_thread(_send_email, alert, target, settings)
                else:
                    await _post_webhook(alert, target)
                self.sent += 1
                await self._update(delivery_id, "delivered", attempt, None)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {exc}"[:300]
                await self._update(delivery_id, "retrying", attempt, error)
                if attempt < settings.notify_max_attempts:
                    await asyncio.sleep(settings.notify_backoff_s * 2 ** (attempt - 1))
        self.failed += 1
        log.warning("alert %s: %s delivery failed after %d attempts", alert["id"], channel, settings.notify_max_attempts)
        await self._update(delivery_id, "failed", settings.notify_max_attempts, error)

    @staticmethod
    async def _record(alert_id: str, channel: str, target: str, status: str, attempts: int) -> str:
        delivery_id = new_id()
        async with session_factory()() as db:
            db.add(AlertDelivery(id=delivery_id, alert_id=alert_id, channel=channel, target=target,
                                 status=status, attempts=attempts))
            await db.commit()
        return delivery_id

    @staticmethod
    async def _update(delivery_id: str, status: str, attempts: int, error: str | None) -> None:
        async with session_factory()() as db:
            await db.execute(
                update(AlertDelivery).where(AlertDelivery.id == delivery_id).values(
                    status=status, attempts=attempts, last_error=error, updated_at=utcnow())
            )
            await db.commit()


def _alert_text(alert: dict[str, Any]) -> str:
    demo = " [DEMO / REPLAY]" if alert.get("is_demo") else ""
    return (
        f"CardioSense {alert['tier'].upper()} alert{demo}: {alert['title']} (rule {alert['rule_id']}).\n"
        f"Alert id: {alert['id']}\nRaised: {alert['created_at']}\n\n"
        "Open the CardioSense console to review the evidence and acknowledge.\n\n"
        f"{DISCLAIMER}"
    )


def _send_email(alert: dict[str, Any], to: str, settings: Settings) -> None:
    msg = EmailMessage()
    msg["Subject"] = f"[CardioSense] {alert['tier'].upper()} alert needs review"
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg.set_content(_alert_text(alert))
    assert settings.smtp_host is not None
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password or "")
        smtp.send_message(msg)


async def _post_webhook(alert: dict[str, Any], url: str) -> None:
    payload = {"event": "alert.created", "alert": alert, "disclaimer": DISCLAIMER}
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
