# Rhythmiq — R1 software core

Live ECG → beat detection and classification → rhythm rules → risk and alerts → clinician console.
Research and demonstration system — not a medical device. Not an emergency service.
(Backend package paths still use `cardiosense` historically.)

## Run

```
scripts/dev.sh          # PostgreSQL + backend :8000 + console :5173
scripts/dev.sh stop
```

Open http://localhost:5173 and sign in as `clinician@cardiosense.dev` / `CardioSense!23`
(also `admin@`, `tech@`, `patient@`). Start a replay from the live console's demo controls, or from a terminal:

```
~/.venvs/cardiosense/bin/python sender/stream_sender.py --file data/records/200.csv \
  --device dev-demo-200 --key demo-key-200 --url ws://localhost:8000 --speed 1
```

Hardware (Arduino + AD8232): flash `sender/firmware/cardiosense_ad8232.ino`, then run `sender/serial_bridge.py --port ...`.

## Layout

| Path | What |
|---|---|
| `sender/` | Replay sender, serial bridge, firmware, multi-stream soak tool (`sender/README.md`) |
| `backend/cardiosense/core/` | Streaming detection: R-peaks, beat model, rules, risk, alerts, signal quality |
| `backend/cardiosense/` | FastAPI app, PostgreSQL models, ingest/live WebSockets, Insights copilot, Dr. Rhidoy guide, reports |
| `frontend/` | React console + landing (`npm run dev:mock` runs it without a backend) |
| `ml/` | Shared inference module, local training script, model directory (`ml/README.md`) |
| `data/records/` | Patient recordings as text: `<id>.csv` + `<id>annotations.txt` (`scripts/fetch_records.py` adds more) |
| `docs/CONTRACT.md` | Wire formats and API between the components |

## Config

Copy `backend/.env.example` → `backend/.env` (gitignored).

| Concern | Env | Notes |
|---|---|---|
| Insights copilot (cited patient answers) | `ANTHROPIC_API_KEY` | Without a key, rules-based templates are used |
| **Dr. Rhidoy** product guide (floating chathead on signed-in pages) | `GROQ_API_KEY` or `LLM_API_KEY`, plus `LLM_BASE_URL` / `LLM_MODEL` (or `GROQ_MODEL`) | OpenAI-compatible; Groq by default. Swap URL + key + model for DeepSeek later. Chip/FAQ answers work without a key; free-text falls back to FAQ on LLM failure |

Dr. Rhidoy is a UI/product guide for any signed-in role — not a diagnosis tool. Insights remains the evidence-cited clinical documentation path.

## Model

`ml/models/cardiosense_beat_model/` currently holds a **stand-in** trained locally on CPU (MIT-BIH only, 1 seed).
To use the Kaggle model, copy its `beat_model_ts.pt` and `model_config.json` into that directory and restart the backend.

## Tests

```
cd backend && ~/.venvs/cardiosense/bin/python -m pytest tests -q
~/.venvs/cardiosense/bin/python -m pytest sender/tests -q
cd frontend && npm run test && npm run typecheck
```

## Known limitations

- The notebook's R-peak detector finds only ~74% of beats on MIT-BIH record 208; use 119, 200 or 233 for alert demos.
- Insights copilot uses the template generator unless `ANTHROPIC_API_KEY` is set; that LLM path is lightly exercised.
- Dr. Rhidoy needs `GROQ_API_KEY` / `LLM_API_KEY` for free-text streaming; suggested chips use local FAQ text either way.
- Not built: password reset / email verification, MFA enrolment screen, scalogram panel, admin user-creation form.
- The firmware has not been compiled or flashed; the serial bridge was tested against a simulated port only.
