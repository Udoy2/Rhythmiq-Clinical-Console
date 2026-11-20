# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary users are clinicians (and demo presenters acting as clinicians) watching a live or replayed ECG stream in a browser console during a research/hackathon demonstration. Their job is to see every beat labelled, notice rhythm risk changes with evidence, triage alerts, and ask a grounded copilot what changed.

Secondary roles: org admin, technician, limited patient view. Redesign prioritises the clinician / demo path.

## Product Purpose

Rhythmiq turns a low-cost ECG stream into a live clinical workspace: beat detection → rhythm rules → risk and alerts → history and cited insight.

Success for R1 / hackathon: a working signed-in web console that streams, labels, escalates, and explains — research/demo, not a medical device.

## Positioning

Honest, patient-wise beat AI plus deterministic rhythm rules; uncertainty as Unknown; copilot cites history and never diagnoses.

## Operating Context

Desktop-first browser during demos and development; tablet and phone widths must stay usable. Mock mode available without a backend.

## Capabilities and Constraints

- Vite + React + TypeScript + Tailwind; FastAPI; WebSocket live feed.
- Design work is frontend-only (`frontend/`); backend and ML are owned by others.
- Not a medical device; disclaimer visible.
- Beat/tier encoding: shape + pattern + color, never colour alone.
- AI insight cited; no invented diagnoses.

## Brand Commitments

- Product name: **Rhythmiq**
- Voice: precise, clinical, non-alarmist
- Binding: the **Rhythmiq** name and the mark from `rhythmiq_brand_kit`.
- Not binding: the kit's colours and fonts (navy `#0B1F3A`, green `#00B86B`, cyan `#22D3EE`, Manrope + DM Sans) are reference only and may change.

## Evidence on Hand

- `rhythmiq_complete_website_brand_kit/rhythmiq_brand_kit/`, `README.md`, `docs/CONTRACT.md`, `frontend/`, `data/records/`
- Do not fabricate testimonials, clearance, or unverified claims

## Product Principles

1. Evidence before drama
2. Redundant clinical encoding (shape + pattern + colour)
3. Honesty about limits
4. Demo-ready console without sacrificing task clarity
5. One coherent console

## Accessibility & Inclusion

**[inferred]** Keep focus-visible; risk/beats must not rely on colour alone.
