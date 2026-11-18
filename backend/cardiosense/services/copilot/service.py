"""Copilot generation (FR-13 / FR-14): LLM behind a provider-agnostic adapter, rules-based template fallback,
then citation enforcement and the safety filter. The language model only ever sees pseudonymised evidence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from cardiosense.config import DISCLAIMER, TIER_RANK, get_settings
from cardiosense.db import new_id, utcnow
from cardiosense.models import Insight, Patient, User
from cardiosense.services.copilot.context import CopilotContext, build_context, short
from cardiosense.services.copilot.guard import enforce_citations, limit_words, safety_violations
from cardiosense.services.ingest import IngestManager
from cardiosense.services.serialize import iso

log = logging.getLogger("cardiosense.copilot")

TEMPLATE_MODEL = "rules-template-1.0"
INSIGHT_DISCLAIMER = f"AI-generated summary for clinician review; not a diagnosis. {DISCLAIMER}"
INSUFFICIENT = "There is not enough recorded evidence for this patient to answer that."

# BRD §8.6 system prompt extract, plus output-format rules the guardrails rely on.
SYSTEM_PROMPT = (
    "You are a clinical documentation assistant. Use only the supplied evidence. Do not diagnose, prescribe, or "
    "give emergency instructions. If the evidence is insufficient, say so. Every factual sentence must cite an "
    "evidence ID in square brackets. Maximum 120 words.\n\n"
    "Further rules:\n"
    "- Cite with the exact IDs given, e.g. [W-1a2b3c4d] or [T-hr], placed before the sentence's full stop. "
    "Never invent an ID.\n"
    "- Cover, where the evidence allows: what changed, how it compares with the baseline, what a clinician may "
    "want to review, and a confidence qualifier based on signal quality.\n"
    "- Describe findings with the labels used in the evidence (for example \"V run\", \"irregular rhythm — "
    "review\"). Never name a disease or condition, never state or imply a diagnosis, never mention medication, "
    "procedures or urgent-care instructions.\n"
    "- Refer to the person only as \"the patient\". Plain prose, no lists, no markdown, no headings."
)


class InsightGenerator(Protocol):
    """Provider-agnostic generator: returns raw text that still has to pass the guardrails."""

    name: str

    async def generate(self, ctx: CopilotContext, question: str | None) -> str: ...


def build_prompt(ctx: CopilotContext, question: str | None) -> str:
    lines = [f"Patient: {ctx.patient_descriptor}.", "", "Evidence (cite by ID):"]
    lines += [f"[{e.id}] {e.fact}" for e in ctx.evidence]
    lines += ["", f"Clinician question: {question.strip()}" if question and question.strip()
              else "Task: summarise what changed for this patient and what merits review."]
    lines.append("Answer in at most 120 words, citing evidence IDs in every factual sentence.")
    return "\n".join(lines)


class AnthropicGenerator:
    """Claude via the official ``anthropic`` SDK. Short timeout, no retries: the template is the fallback."""

    def __init__(self, api_key: str, model: str, timeout_s: float) -> None:
        import anthropic  # noqa: PLC0415 - optional dependency path

        self._anthropic = anthropic
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout_s, max_retries=0)
        self.name = model

    async def generate(self, ctx: CopilotContext, question: str | None) -> str:
        request: dict[str, Any] = {
            "model": self.name,
            "max_tokens": 2000,  # leaves room for the model's thinking; the answer itself is <= 120 words
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": build_prompt(ctx, question)}],
        }
        try:
            response = await self._client.messages.create(**request, output_config={"effort": "low"})
        except self._anthropic.BadRequestError:
            # Models that do not take an effort setting: retry once with the plain request.
            response = await self._client.messages.create(**request)
        if response.stop_reason == "refusal":
            raise RuntimeError("model declined the request")
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if not text:
            raise RuntimeError("model returned no text")
        return text


class TemplateGenerator:
    """Deterministic rules-based summary built from the same evidence, with the same citation ids."""

    name = TEMPLATE_MODEL

    async def generate(self, ctx: CopilotContext, question: str | None) -> str:
        return " ".join(self.sentences(ctx))

    @staticmethod
    def sentences(ctx: CopilotContext) -> list[str]:
        ids = ctx.by_id()
        out: list[str] = []
        recent, base = ctx.recent, ctx.baseline

        # 1. current state
        w = ctx.latest_window
        if w is not None and f"W-{short(w.id)}" in ids:
            labels = ", ".join(f.get("label", "") for f in (w.findings or [])[:3])
            hr = f"HR {w.hr_bpm:.0f} bpm, " if w.hr_bpm is not None else ""
            out.append(
                f"The latest rhythm window shows {hr}V burden {w.v_burden_pct or 0:.1f}% and a risk score of "
                f"{w.risk_score}/100 ({w.risk_tier})" + (f", with {labels}" if labels else ", with no rule findings")
                + f" [W-{short(w.id)}]."
            )
        elif "T-recent" in ids:
            out.append(f"Monitoring data is on record for {ctx.recent_label} [T-recent].")

        # 2. what changed versus baseline
        changes = []
        for key, tid, name, unit, digits, threshold in (
            ("v_burden_pct", "T-vburden", "mean V burden", "%", 1, 0.5),
            ("s_burden_pct", "T-sburden", "mean S burden", "%", 1, 0.5),
            ("hr_bpm", "T-hr", "mean heart rate", " bpm", 0, 3.0),
            ("risk_score_mean", "T-risk", "mean risk score", "", 0, 5.0),
        ):
            a, b = recent.get(key), base.get(key)
            if tid not in ids or a is None or b is None:
                continue
            if abs(a - b) >= threshold:
                direction = "rose" if a > b else "fell"
                changes.append(f"{name} {direction} from {b:.{digits}f}{unit} to {a:.{digits}f}{unit} [{tid}]")
        if changes:
            out.append(f"Compared with the 14-day baseline, {'; '.join(changes[:3])}.")
        elif "T-baseline" in ids and "T-recent" in ids:
            out.append("Heart rate, ectopic burden and risk score are close to the 14-day baseline "
                       "[T-recent][T-baseline].")
        elif "T-recent" in ids:
            out.append("No earlier monitoring is on record, so there is no baseline to compare against [T-recent].")

        # 3. alerts
        alerts = sorted(ctx.alerts, key=lambda a: (-TIER_RANK.get(a.tier, 0), a.state != "open"))
        if alerts:
            unresolved = [a for a in alerts if a.state in ("open", "escalated")]
            head = alerts[0]
            cites = "".join(f"[A-{short(a.id)}]" for a in alerts[:3])
            out.append(
                f"{len(alerts)} alert{'s are' if len(alerts) != 1 else ' is'} on record for this period, "
                f"{len(unresolved)} not yet resolved; the highest tier is \"{head.title}\" ({head.tier}, {head.state}) "
                f"{cites}."
            )

        # 4. notes
        if ctx.notes:
            symptoms = sum(1 for n in ctx.notes if n.kind == "symptom")
            others = len(ctx.notes) - symptoms
            parts = []
            if symptoms:
                parts.append(f"{symptoms} patient-reported symptom entr{'ies' if symptoms != 1 else 'y'}")
            if others:
                parts.append(f"{others} clinician note{'s' if others != 1 else ''}")
            cites = "".join(f"[N-{short(n.id)}]" for n in ctx.notes[:3])
            out.append(f"Recent records include {' and '.join(parts)} {cites}.")

        # 5. what to review
        review = []
        unresolved = [a for a in alerts if a.state in ("open", "escalated")]
        if unresolved:
            review.append(f"the evidence strip of the unresolved alert [A-{short(unresolved[0].id)}]")
        if any(c.endswith("[T-vburden]") for c in changes):
            review.append("the V burden trend [T-vburden]")
        if review:
            out.append(f"For review: {' and '.join(review)}.")

        # 6. confidence qualifier
        if "Q-recent" in ids and recent.get("sqi") is not None:
            quality = "good" if recent["sqi"] >= 0.8 else "moderate" if recent["sqi"] >= 0.5 else "low"
            out.append(
                f"Signal quality was {quality} (mean SQI {recent['sqi']:.2f}); beat labels are model estimates that "
                f"need clinician review [Q-recent]."
            )
        return out


@dataclass
class CopilotResult:
    text: str
    citations: list[dict[str, Any]]
    generated_by: str
    model: str
    guard: dict[str, Any] = field(default_factory=dict)


def _apply_guardrails(raw: str, ctx: CopilotContext) -> tuple[str, list[str], dict[str, Any]]:
    """Citation enforcer -> word limit -> safety filter. Returns (text, cited ids, report)."""
    valid = set(ctx.by_id())
    sentences, _, dropped = enforce_citations(raw, valid)
    sentences = limit_words(sentences)
    text = " ".join(sentences)
    _, used, _ = enforce_citations(text, valid)
    report = {"sentences_dropped_uncited": dropped, "safety_violations": safety_violations(text)}
    return text, used, report


def default_generator() -> InsightGenerator | None:
    settings = get_settings()
    if not settings.llm_enabled or not settings.anthropic_api_key:
        return None
    try:
        return AnthropicGenerator(settings.anthropic_api_key, settings.llm_model, settings.llm_timeout_s)
    except Exception as exc:  # noqa: BLE001 - SDK missing or misconfigured: template only
        log.warning("LLM adapter unavailable (%s); using the rules-based template", type(exc).__name__)
        return None


async def generate_insight(
    ctx: CopilotContext, question: str | None, llm: InsightGenerator | None
) -> CopilotResult:
    if not ctx.evidence:
        return CopilotResult(INSUFFICIENT, [], "template", TEMPLATE_MODEL, {"reason": "no evidence retrieved"})
    by_id = ctx.by_id()
    guard: dict[str, Any] = {}
    if llm is not None:
        try:
            raw = await llm.generate(ctx, question)
            text, used, report = _apply_guardrails(raw, ctx)
            guard["llm"] = report
            if text and not report["safety_violations"]:
                return CopilotResult(text, [by_id[i].citation() for i in used], "llm", llm.name, guard)
            guard["fallback_reason"] = "safety filter" if report["safety_violations"] else "no cited sentences"
            log.warning("copilot LLM output rejected (%s); using template", guard["fallback_reason"])
        except Exception as exc:  # noqa: BLE001 - timeout, rate limit, outage, refusal: same fallback
            guard["fallback_reason"] = f"llm error: {type(exc).__name__}"
            log.warning("copilot LLM call failed (%s); using template", type(exc).__name__)
    raw = await TemplateGenerator().generate(ctx, question)
    text, used, report = _apply_guardrails(raw, ctx)
    guard["template"] = report
    if not text or report["safety_violations"]:  # cannot happen with the fixed template; fail closed regardless
        return CopilotResult(INSUFFICIENT, [], "template", TEMPLATE_MODEL, guard)
    return CopilotResult(text, [by_id[i].citation() for i in used], "template", TEMPLATE_MODEL, guard)


def insight_to_dict(row: Insight) -> dict[str, Any]:
    return {
        "id": row.id, "patient_id": row.patient_id, "session_id": row.session_id, "question": row.question,
        "text": row.text, "citations": row.citations or [], "generated_by": row.generated_by, "model": row.model,
        "generated_at": iso(row.generated_at), "disclaimer": INSIGHT_DISCLAIMER, "guard": row.guard or {},
    }


async def answer(
    db: AsyncSession, ingest: IngestManager, patient: Patient, question: str | None, user: User,
    llm: InsightGenerator | None,
) -> Insight:
    """Build context, generate, guard, and store the insight (the caller commits)."""
    ctx = await build_context(db, ingest, patient)
    result = await generate_insight(ctx, question, llm)
    row = Insight(
        id=new_id(), patient_id=patient.id, session_id=ctx.session_id, question=question or None, text=result.text,
        citations=result.citations, generated_by=result.generated_by, model=result.model, guard=result.guard,
        created_by=user.id, generated_at=utcnow(),
    )
    db.add(row)
    return row
