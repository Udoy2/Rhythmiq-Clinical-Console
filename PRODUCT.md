# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

**[inferred]** Primary users are clinicians (and demo presenters acting as clinicians) watching a live or replayed ECG stream in a browser console during a research/hackathon demonstration. Their job is to see every beat labelled, notice rhythm risk changes with evidence, triage alerts, and ask a grounded copilot what changed.

**[inferred]** Secondary roles: org admin, technician, limited patient view. Redesign prioritises the clinician / demo path.

## Product Purpose

Rhythmiq turns a low-cost ECG stream into a live clinical workspace: beat detection → rhythm rules → risk and alerts → history and cited insight.

Success for R1 / hackathon: a working signed-in web console that streams, labels, escalates, and explains — research/demo, not a medical device.

## Positioning

Honest, patient-wise beat AI plus deterministic rhythm rules; uncertainty as Unknown; copilot cites history and never diagnoses.

## Operating Context

Desktop browser during demos and development. Mock mode available without a backend.

## Capabilities and Constraints

- Vite + React + TypeScript + Tailwind; FastAPI; WebSocket live feed.
- Not a medical device; disclaimer visible.
- Beat/tier encoding: shape + pattern + color, never colour alone.
- AI insight cited; no invented diagnoses.

## Brand Commitments

- Product name: **Rhythmiq**
- Voice: precise, clinical, non-alarmist
- Visual: **official website brand kit** — navy `#0B1F3A`, medical green `#00B86B`, cyan `#22D3EE`, light `#F5F7FA`, Manrope + DM Sans, mark from `rhythmiq_brand_kit`

## Evidence on Hand

- `rhythmiq_complete_website_brand_kit/rhythmiq_brand_kit/`, `README.md`, `docs/CONTRACT.md`, `frontend/`, `data/records/`
- Do not fabricate testimonials, clearance, or unverified claims

## Product Principles

1. Evidence before drama
2. Redundant clinical encoding (shape + pattern + colour)
3. Honesty about limits
4. Brand-true console for demos without sacrificing task clarity
5. One coherent console

## Accessibility & Inclusion

**[inferred]** Keep focus-visible; risk/beats must not rely on colour alone.
