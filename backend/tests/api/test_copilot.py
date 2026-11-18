"""Copilot guardrails (FR-13/14, BRD §8.6): citations are enforced, unsafe wording falls back to the template,
and the language model never sees patient identifiers."""

from __future__ import annotations

import re

import pytest
import pytest_asyncio

from cardiosense.services.copilot.guard import enforce_citations, limit_words, safety_violations, split_sentences

pytestmark = pytest.mark.asyncio(loop_scope="session")

CITE = re.compile(r"\[([A-Z]-[A-Za-z0-9-]+)\]")


# ------------------------------------------------------------------ guard unit tests

async def test_citation_enforcer_drops_uncited_and_unknown_ids():
    text = ("V burden rose to 19% [T-vburden]. The patient is probably fine. An alert is open [A-11112222, X-bogus]. "
            "This cites nothing real [Z-nope]. Quality was good. [Q-recent] Trailing claim without a source.")
    kept, used, dropped = enforce_citations(text, {"T-vburden", "A-11112222", "Q-recent"})
    assert kept == ["V burden rose to 19% [T-vburden].", "An alert is open [A-11112222].", "Quality was good. [Q-recent]"]
    assert used == ["T-vburden", "A-11112222", "Q-recent"] and dropped == 3
    assert enforce_citations("Nothing cited at all.", {"T-hr"}) == ([], [], 1)
    assert split_sentences("One [A-1]. Two [B-2]!  Three?") == ["One [A-1].", "Two [B-2]!", "Three?"]


@pytest.mark.parametrize(("text", "category"), [
    ("These findings are diagnostic of a rhythm disorder [W-1].", "diagnostic"),
    ("The rhythm is consistent and the patient has atrial fibrillation [W-1].", "diagnostic"),
    ("This looks like AFib [W-1].", "diagnostic"),
    ("The patient is having an arrhythmia [W-1].", "diagnostic"),
    ("Start a beta-blocker at a low dose [A-1].", "prescriptive"),
    ("Consider amiodarone [A-1].", "prescriptive"),
    ("Give 5 mg now [A-1].", "prescriptive"),
    ("Call 911 immediately [A-1].", "emergency"),
    ("The patient should go to the emergency department [A-1].", "emergency"),
    ("Seek immediate medical attention [A-1].", "emergency"),
])
async def test_safety_filter_blocks(text, category):
    assert category in safety_violations(text)


async def test_safety_filter_allows_neutral_evidence_language():
    ok = ("Mean V burden rose from 14.1% to 19.3% [T-vburden]. An orange alert \"V run (non-sustained)\" is open "
          "[A-1a2b3c4d]. Irregular rhythm — review was flagged in two windows [W-9f8e7d6c]. For review: the evidence "
          "strip [A-1a2b3c4d]. Signal quality was good; beat labels are model estimates that need clinician review "
          "[Q-recent].")
    assert safety_violations(ok) == []
    assert len(limit_words(["word " * 70 + "[A-1].", "word " * 70 + "[A-2]."])) == 1


# ------------------------------------------------------------------ endpoint

class FakeLLM:
    name = "fake-llm-1"

    def __init__(self, reply):
        self.reply = reply
        self.prompts: list[str] = []

    async def generate(self, ctx, question):
        from cardiosense.services.copilot.service import build_prompt

        self.prompts.append(build_prompt(ctx, question))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply(ctx) if callable(self.reply) else self.reply


@pytest_asyncio.fixture(loop_scope="session")
async def use_llm(app):
    saved = app.state.copilot_llm

    def install(reply) -> FakeLLM:
        app.state.copilot_llm = FakeLLM(reply)
        return app.state.copilot_llm

    yield install
    app.state.copilot_llm = saved


def _assert_grounded(body: dict) -> None:
    assert set(body) >= {"id", "text", "citations", "generated_by", "model", "generated_at", "disclaimer"}
    assert "not a medical device" in body["disclaimer"]
    cited = {c["id"] for c in body["citations"]}
    assert cited and all(set(c) == {"id", "kind", "label", "time"} for c in body["citations"])
    sentences = split_sentences(body["text"])
    assert sentences
    for sentence in sentences:
        ids = CITE.findall(sentence)
        assert ids and set(ids) <= cited, f"uncited sentence: {sentence!r}"
    assert len(CITE.sub("", body["text"]).split()) <= 120
    assert safety_violations(body["text"]) == []


async def test_template_insight_is_cited_safe_and_stored(client, auth, demo_patients):
    clin = await auth.headers("clinician")
    pid = demo_patients["208"]["id"]
    r = await client.post("/copilot/query", json={"patient_id": pid}, headers=clin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["generated_by"] == "template" and body["model"] == "rules-template-1.0"
    _assert_grounded(body)
    kinds = {c["kind"] for c in body["citations"]}
    assert {"window", "trend", "alert"} <= kinds, body
    # citation ids are stable and resolve to real records
    alerts = {a["id"].replace("-", "")[:8] for a in (await client.get("/alerts", params={"patient_id": pid}, headers=clin)).json()}
    assert {c["id"][2:] for c in body["citations"] if c["kind"] == "alert"} <= alerts
    again = (await client.post("/copilot/query", json={"patient_id": pid, "question": "What changed?"}, headers=clin)).json()
    assert {c["id"] for c in again["citations"]} == {c["id"] for c in body["citations"]}

    stored = (await client.get(f"/patients/{pid}/insights", headers=clin)).json()
    assert [s["id"] for s in stored[:2]] == [again["id"], body["id"]] and stored[0]["question"] == "What changed?"

    # a patient with nothing on record gets an explicit "insufficient evidence" answer, not invented content
    empty = (await client.post("/patients", headers=clin, json={"name": "No Data", "consent": True})).json()
    none = (await client.post("/copilot/query", json={"patient_id": empty["id"]}, headers=clin)).json()
    assert none["citations"] == [] and "not enough recorded evidence" in none["text"]


async def test_llm_output_passes_through_the_citation_enforcer(client, auth, demo_patients, use_llm):
    def reply(ctx):
        ids = [e.id for e in ctx.evidence]
        return (f"Mean V burden is higher than the baseline [{ids[0]}]. I think the outlook is excellent. "
                f"An invented source says otherwise [A-deadbeef]. Recent alerts are on record [{ids[-1]}, B-fake].")

    llm = use_llm(reply)
    patient = demo_patients["208"]
    r = await client.post("/copilot/query", json={"patient_id": patient["id"], "question": "What changed since yesterday?"},
                          headers=await auth.headers("clinician"))
    body = r.json()
    assert body["generated_by"] == "llm" and body["model"] == "fake-llm-1"
    _assert_grounded(body)
    assert "outlook" not in body["text"] and "deadbeef" not in body["text"] and "B-fake" not in body["text"]
    assert len(body["citations"]) == 2 and body["guard"]["llm"]["sentences_dropped_uncited"] == 2

    # pseudonymisation: no identifiers reach the model
    prompt = llm.prompts[0]
    assert "What changed since yesterday?" in prompt
    for secret in (patient["name"], patient["mrn"], patient["id"], "Critical", str(patient["dob"])):
        assert secret not in prompt
    assert "age 60-69" in prompt


@pytest.mark.parametrize("bad", [
    "This is atrial fibrillation and the diagnosis is clear [{id}].",
    "Start amiodarone 200 mg daily [{id}].",
    "Call 911 and go to the emergency room now [{id}].",
    "No citations anywhere in this answer.",
    RuntimeError("provider down"),
    TimeoutError("slow"),
])
async def test_unsafe_or_failed_llm_falls_back_to_the_template(client, auth, demo_patients, use_llm, bad):
    use_llm((lambda ctx: bad.format(id=ctx.evidence[0].id)) if isinstance(bad, str) else bad)
    r = await client.post("/copilot/query", json={"patient_id": demo_patients["223"]["id"]},
                          headers=await auth.headers("clinician"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["generated_by"] == "template" and body["guard"]["fallback_reason"]
    _assert_grounded(body)
    for word in ("fibrillation", "amiodarone", "911"):
        assert word not in body["text"]
