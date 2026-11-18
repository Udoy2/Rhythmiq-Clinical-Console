"""Copilot output guardrails (BRD §8.6): citation enforcer, safety filter and the 120-word limit."""

from __future__ import annotations

import re

MAX_WORDS = 120

_CITATION = re.compile(r"\[([^\[\]]{1,120})\]")
_ID = re.compile(r"^[A-Z]-[A-Za-z0-9][A-Za-z0-9-]{0,40}$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])")
_LEADING_CITES = re.compile(r"^((?:\[[^\[\]]{1,120}\]\s*)+)")

# Wording the copilot must never produce. Each entry: (category, pattern).
_SAFETY: list[tuple[str, re.Pattern[str]]] = [
    ("diagnostic", re.compile(r"\bdiagnos\w*", re.I)),
    ("diagnostic", re.compile(
        r"\b(atrial fibrillation|atrial flutter|a-?fib|ventricular tachycardia|v-?tach|ventricular fibrillation|"
        r"v-?fib|heart attack|myocardial infarction|isch[a]?emi\w*|heart failure|cardiomyopathy|long qt|"
        r"heart block|av block|sick sinus|wolff|torsades|stroke|cardiac arrest)\b", re.I)),
    ("diagnostic", re.compile(r"\b(VT|VF|AF|MI|SVT|NSVT)\b")),
    ("diagnostic", re.compile(
        r"\b(has|have|having|suffers?|suffering|experiencing)\s+(from\s+)?(an?\s+)?"
        r"(arrhythmia|cardiac (disease|condition)|heart (disease|condition))\b", re.I)),
    ("diagnostic", re.compile(r"\b(indicative of|suggestive of|pathognomonic|confirms? (an?|the) )", re.I)),
    ("prescriptive", re.compile(r"\bprescri\w*", re.I)),
    ("prescriptive", re.compile(
        r"\b(start|stop|increase|decrease|reduce|adjust|titrate|administer|take|taking|discontinue|initiate|switch)\b"
        r"[^.]{0,50}\b(dose|dosage|medication|medicine|drug|therapy|treatment|beta.?blockers?|anticoagula\w*)\b", re.I)),
    ("prescriptive", re.compile(r"\b\d+(\.\d+)?\s?(mg|mcg|µg|ml|iu)\b", re.I)),
    ("prescriptive", re.compile(
        r"\b(amiodarone|metoprolol|bisoprolol|atenolol|propranolol|digoxin|warfarin|apixaban|rivaroxaban|dabigatran|"
        r"flecainide|sotalol|verapamil|diltiazem|adenosine|lidocaine|aspirin|magnesium sulph?ate)\b", re.I)),
    ("prescriptive", re.compile(r"\b(ablation|cardioversion|pacemaker|defibrillat\w*|ICD implant\w*)\b", re.I)),
    ("prescriptive", re.compile(r"\b(recommend|advise|suggest)\w*\s+(a |the |that |starting |stopping )?"
                                r"(treatment|therapy|medication|admission|hospitali[sz]ation)\b", re.I)),
    ("emergency", re.compile(r"\b(call|dial|phone)\s+(911|999|112|an ambulance|emergency|the emergency)\b", re.I)),
    ("emergency", re.compile(r"\b(911|999)\b")),
    ("emergency", re.compile(r"\bemergency (room|department|services?|care|response)\b", re.I)),
    ("emergency", re.compile(r"\bgo to (the |an |a )?(ER|A&E|hospital|emergency)\b", re.I)),
    ("emergency", re.compile(r"\b(seek|get|needs?|requires?)\s+(immediate|urgent|emergency)\s+"
                             r"(medical |clinical )?(help|care|attention|treatment)\b", re.I)),
    ("emergency", re.compile(r"\b(CPR|life[- ]threatening|medical emergency|ambulance)\b", re.I)),
]


def split_sentences(text: str) -> list[str]:
    """Sentences of ``text``; citation tags that follow the full stop are kept with their sentence."""
    pieces = [p.strip() for p in _SENTENCE_END.split(" ".join(text.split())) if p.strip()]
    out: list[str] = []
    for piece in pieces:
        lead = _LEADING_CITES.match(piece)
        if lead and out:
            out[-1] = f"{out[-1]} {lead.group(1).strip()}"
            piece = piece[lead.end():].strip()
        if piece:
            out.append(piece)
    return out


def cited_ids(sentence: str) -> list[str]:
    ids: list[str] = []
    for group in _CITATION.findall(sentence):
        for part in re.split(r"[,;]\s*", group):
            part = part.strip()
            if _ID.match(part) and part not in ids:
                ids.append(part)
    return ids


def enforce_citations(text: str, valid_ids: set[str]) -> tuple[list[str], list[str], int]:
    """Drop every sentence that does not cite a retrieved evidence id.

    Returns (kept sentences with unknown ids stripped, cited ids in order of first use, sentences dropped).
    """
    kept: list[str] = []
    used: list[str] = []
    dropped = 0
    for sentence in split_sentences(text):
        ids = cited_ids(sentence)
        valid = [i for i in ids if i in valid_ids]
        if not valid:
            dropped += 1
            continue
        if len(valid) != len(ids):  # remove citations of ids that were never retrieved
            def _clean(match: re.Match[str]) -> str:
                parts = [p.strip() for p in re.split(r"[,;]\s*", match.group(1))]
                if not any(_ID.match(p) for p in parts):
                    return match.group(0)
                good = [p for p in parts if p in valid_ids]
                return f"[{', '.join(good)}]" if good else ""
            sentence = " ".join(_CITATION.sub(_clean, sentence).split())
        kept.append(sentence)
        for i in valid:
            if i not in used:
                used.append(i)
    return kept, used, dropped


def safety_violations(text: str) -> list[str]:
    """Categories of forbidden wording found in ``text`` (diagnostic / prescriptive / emergency)."""
    plain = _CITATION.sub(" ", text)  # evidence ids like [A-1f2e] are not prose
    found: list[str] = []
    for category, pattern in _SAFETY:
        if category not in found and pattern.search(plain):
            found.append(category)
    return found


def limit_words(sentences: list[str], max_words: int = MAX_WORDS) -> list[str]:
    """Keep whole sentences while the total stays within the word limit (citation tags are not words)."""
    out: list[str] = []
    total = 0
    for sentence in sentences:
        words = len(_CITATION.sub(" ", sentence).split())
        if out and total + words > max_words:
            break
        out.append(sentence)
        total += words
    return out
