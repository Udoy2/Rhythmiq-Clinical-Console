"""Dr. Rhidoy — in-app product guide (OpenAI-compatible LLM; FAQ fallback)."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx

from cardiosense.config import get_settings

PRODUCT_FACTS = """
Product facts (use only these; do not invent UI):

Brand: Rhythmiq (research / demonstration console — not a medical device).

Global (signed-in):
- Patients list: open a DEMO patient, see risk tier, start live monitoring.
- Live console (/patients/:id/live): ECG strip, beat ribbon (N/S/V/F/Unknown), rhythm window,
  risk gauge with findings, demo replay controls (MIT-BIH record, speed, Start/Stop).
- History / sessions: past monitoring sessions, events, CSV export.
- AI insights (/patients/:id/insights): cited copilot answers from the patient record (clinician role).
  Dr. Rhidoy is the UI guide; Insights is the evidence-cited clinical documentation tool — do not confuse them.
- Reports: PDF summary for a date range.
- Alerts centre: open / ack / escalate / close alerts with evidence snapshots (clinician+).
- Admin / Devices: users, devices, pairing, rules (role-gated).
- About the model: model card and evaluation notes.
- Theme toggle and sign-out live in the shell.

Demo accounts (password from seed, typically CardioSense!23):
- clinician@cardiosense.dev — live console, alerts, insights
- admin@cardiosense.dev — users, devices, audit
- tech@cardiosense.dev — device pairing
- patient@cardiosense.dev — limited patient view

Beat labels: N normal, S supraventricular ectopic, V ventricular ectopic, F fusion, Unknown low confidence.
Risk tiers: green / amber / orange / red with evidence — never colour alone.
"""

SYSTEM_PROMPT = f"""You are Dr. Rhidoy, the in-app guide for Rhythmiq (research demo, not a medical device).

Role:
- Help any signed-in role use this console using PRODUCT FACTS only.
- When discussing rhythm or beats, stay in decision-support language only.
- Point clinicians to AI Insights for cited patient-record questions; you are the product guide.

{PRODUCT_FACTS}

Style:
- Target 40–70 words. Hard cap 90 words.
- Lead with the useful answer in the first sentence.
- Short bullets only when listing labels or sections (max 4).
- No filler closings like "Let me know if you…".
- Always end on a complete sentence.

Hard rules:
- Do NOT diagnose, prescribe, or give emergency instructions.
- Do NOT invent UI features, buttons, or patient history.
- If patient-event evidence is not supplied, stay on product guidance.
- Never claim to be a licensed physician making treatment decisions.

Tone: calm, clear, clinical UI guide."""

FAQ_ITEMS: list[dict[str, str]] = [
    {
        "id": "start-monitor",
        "label": "How do I start live monitoring?",
        "answer": (
            "Open Patients, choose a DEMO patient, then Live. Pick a MIT-BIH record and speed, "
            "press Start replay. Use Stop to end the stream. Decision support only — not a diagnosis."
        ),
    },
    {
        "id": "ecg-strip",
        "label": "What is the ECG strip?",
        "answer": (
            "The ECG strip is the live scrolling waveform. Beat markers sit on the trace; "
            "the beat ribbon under it shows recent N/S/V/F/Unknown labels. Demo decision support only."
        ),
    },
    {
        "id": "monitor-sections",
        "label": "What do the live console panels mean?",
        "answer": (
            "Strip + ribbon = waveform and labels. Rhythm = 10 s rules (HR, SQI, findings). "
            "Risk gauge = 0–100 score and tier with contributing findings. Alerts open when rules fire."
        ),
    },
    {
        "id": "beat-labels",
        "label": "What do the beat labels mean?",
        "answer": (
            "N = normal, S = supraventricular ectopic, V = ventricular ectopic, F = fusion, "
            "Unknown = low confidence. Labels are decision support — not a diagnosis."
        ),
    },
    {
        "id": "insights-vs-guide",
        "label": "Insights vs Dr. Rhidoy?",
        "answer": (
            "Dr. Rhidoy explains how to use the console. AI Insights (clinician) answers from the "
            "patient record with citations. Use Insights for record questions; use me for UI help."
        ),
    },
    {
        "id": "alerts",
        "label": "How do alerts work?",
        "answer": (
            "When a rhythm rule fires, an alert appears with an evidence snapshot. "
            "Open Alerts to acknowledge, escalate, or close. Not an emergency service."
        ),
    },
    {
        "id": "demo-accounts",
        "label": "Which demo account should I use?",
        "answer": (
            "Clinician for live console, alerts, and insights. Admin for users and devices. "
            "Technician for pairing. Patient for the limited patient view. Seed password is in About / README."
        ),
    },
    {
        "id": "cannot-do",
        "label": "What can't Dr. Rhidoy do?",
        "answer": (
            "I cannot diagnose, prescribe, or give emergency instructions, and I must not invent UI features. "
            "I explain Rhythmiq screens that exist in this demo. Research decision support only."
        ),
    },
]

LIVE_SUGGESTION_IDS = ["ecg-strip", "monitor-sections", "beat-labels", "insights-vs-guide", "cannot-do"]
DEFAULT_SUGGESTION_IDS = ["start-monitor", "beat-labels", "alerts", "demo-accounts", "cannot-do"]
INSIGHTS_SUGGESTION_IDS = ["insights-vs-guide", "start-monitor", "beat-labels", "cannot-do"]


def _faq_by_id(faq_id: str) -> dict[str, str] | None:
    for item in FAQ_ITEMS:
        if item["id"] == faq_id:
            return item
    return None


def page_context(page: str | None) -> str:
    path = (page or "").lower()
    if "/live" in path:
        return (
            "User is on the Live console. Explain strip, ribbon, rhythm, risk, demo Start/Stop. "
            "For cited clinical questions, point to AI Insights."
        )
    if "/insights" in path:
        return "User is on AI Insights. Clarify guide vs cited copilot; help them ask evidence-bound questions there."
    if "/alerts" in path:
        return "User is on Alerts. Explain open/ack/escalate/close and evidence snapshots."
    if "/patients" in path and path.rstrip("/").endswith("patients"):
        return "User is on Patients list. Help them open a DEMO patient and start Live."
    if "/history" in path or "/sessions" in path:
        return "User is on History/session detail. Explain past sessions, events, and CSV export."
    if "/reports" in path:
        return "User is on Reports. Explain PDF export and the disclaimer."
    if "/admin" in path:
        return "User is on Admin/Devices. Explain users, pairing, and rules at a high level."
    if "/about" in path:
        return "User is on About the model. Explain model card / evaluation notes briefly."
    return "User page unknown; stick to PRODUCT FACTS."


def suggestions(page: str | None = None) -> list[dict[str, str]]:
    path = (page or "").lower()
    if "/live" in path:
        ids = LIVE_SUGGESTION_IDS
    elif "/insights" in path:
        ids = INSIGHTS_SUGGESTION_IDS
    else:
        ids = DEFAULT_SUGGESTION_IDS
    out: list[dict[str, str]] = []
    for faq_id in ids:
        item = _faq_by_id(faq_id)
        if item:
            out.append({"id": item["id"], "label": item["label"]})
    return out


def faq_fallback(message: str, page: str | None = None) -> str | None:
    text = message.lower().strip()
    for item in FAQ_ITEMS:
        if item["label"].lower() == text:
            return item["answer"]

    rules: list[tuple[str, list[str]]] = [
        ("insights-vs-guide", ["insights", "copilot", "dr. rhidoy", "difference", "vs"]),
        ("ecg-strip", ["ecg strip", "waveform", "scrolling", "trace"]),
        ("monitor-sections", ["monitor section", "panels", "beat ribbon", "risk gauge", "rhythm status"]),
        ("beat-labels", ["beat label", "n/s/v", "unknown", "ventricular", "supraventricular"]),
        ("alerts", ["alert", "acknowledge", "escalate"]),
        ("demo-accounts", ["demo account", "which account", "password", "clinician@"]),
        ("cannot-do", ["can't you", "cannot you", "what can't", "diagnose", "prescribe", "emergency"]),
        ("start-monitor", ["start live", "start monitor", "start replay", "how do i start"]),
    ]
    for faq_id, keys in rules:
        if any(k in text for k in keys):
            item = _faq_by_id(faq_id)
            if item:
                return item["answer"]

    if "/live" in (page or "").lower() and any(
        k in text for k in ("this page", "this screen", "what am i looking", "explain this")
    ):
        item = _faq_by_id("monitor-sections")
        if item:
            return item["answer"]
    return None


def default_fallback() -> str:
    return (
        "I can explain Patients, Live console, beat labels, alerts, and how Insights differs from this guide. "
        "I do not diagnose or invent missing controls."
    )


def build_messages(
    user_message: str,
    history: list[dict[str, str]] | None = None,
    page: str | None = None,
) -> list[dict[str, str]]:
    context = f"Current UI page: {page or 'unknown'}\n{page_context(page)}"
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT + "\n" + context}]
    for turn in (history or [])[-8:]:
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content[:2000]})
    messages.append({"role": "user", "content": user_message.strip()[:4000]})
    return messages


async def stream_chat(messages: list[dict[str, str]]) -> AsyncIterator[str]:
    """Stream tokens from any OpenAI-compatible chat API (Groq today, DeepSeek later)."""
    settings = get_settings()
    if not settings.llm_api_key:
        raise RuntimeError("LLM_API_KEY not configured")

    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": settings.assistant_llm_model,
        "messages": messages,
        "temperature": 0.25,
        "max_tokens": 1024,
        "stream": True,
    }
    # Groq gpt-oss optional knobs; harmless to omit for DeepSeek
    if "gpt-oss" in settings.assistant_llm_model:
        payload["reasoning_effort"] = "low"

    async with httpx.AsyncClient(timeout=settings.assistant_llm_timeout_s) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            if resp.status_code >= 400:
                body = await resp.aread()
                raise RuntimeError(f"LLM error {resp.status_code}: {body.decode()[:400]}")
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                text = delta.get("content")
                if text:
                    yield text


_DIAG_RE = re.compile(
    r"\b(you have|diagnos(?:e|is|ed)|prescrib(?:e|ing)|take \d+ ?mg|call 911|go to (?:the )?er)\b",
    re.I,
)


def safety_trim(text: str) -> str:
    cleaned = text.strip()
    if _DIAG_RE.search(cleaned):
        return (
            "I can't provide diagnoses, prescriptions, or emergency instructions. "
            "Please review the monitoring console and escalate through your clinical workflow. "
            "I can still help with how to use Rhythmiq."
        )
    cleaned = re.sub(
        r"(?:Let me know if you|Feel free to ask(?: me)?(?: if)?|If you (?:need|want|have)|Would you like me to).*$",
        "",
        cleaned,
        flags=re.I,
    ).rstrip(" ,;:-")
    words = cleaned.split()
    if len(words) > 95:
        clipped = " ".join(words[:90]).rstrip(" ,;:-")
        for end in (". ", "! ", "? ", ".\n", "!\n", "?\n"):
            idx = clipped.rfind(end)
            if idx >= 24:
                return clipped[: idx + 1].strip()
        if re.search(r"[.!?]$", clipped):
            return clipped
        return clipped + "."
    if cleaned and not re.search(r"[.!?]$", cleaned):
        cleaned += "."
    return cleaned
