# CardioSense AI — build contract (R1 software core)

Single source of truth for interfaces between components. If you must deviate, keep the
wire formats below intact and note the deviation in your final report.

Product spec: `CardioSense_AI_BRD_v1.1.md` (repo root). Model provenance: `main_notebook.ipynb`.

## 1. Layout and environment

```
ecgArithmia/
  ml/        cardiosense_beats.py (shared preprocessing, from the notebook — do not change behaviour)
             train_local.py, models/cardiosense_beat_model/{beat_model_ts.pt, model_config.json, ...}
  backend/   Python package `cardiosense` (FastAPI app, pipeline core, tests)
               cardiosense/core/   <- pipeline core (no DB, no web imports)
               cardiosense/api|db|services/...  <- API layer
  sender/    stream sender / serial bridge CLIs (standalone, only needs websockets, numpy, pyserial)
  frontend/  Vite + React + TypeScript + Tailwind SPA
  data/records/  patient recordings: `<id>.csv` + `<id>annotations.txt` (MIT-BIH exported as text)
  scripts/   dev helpers
```

- Python: use the existing venv `~/.venvs/cardiosense/bin/python` (3.12). Already installed: torch (CPU),
  fastapi, uvicorn[standard], sqlalchemy[asyncio], asyncpg, psycopg2-binary, pydantic-settings, argon2-cffi,
  pyjwt, pyotp, numpy, scipy, pandas, scikit-learn, wfdb, websockets, httpx, pytest, pytest-asyncio,
  reportlab, pyserial, anthropic, python-multipart, tqdm. Install anything else with that venv's pip and list
  it in `backend/requirements.txt`. Never create a venv inside the repo (it is a slow Windows 9p mount).
- PostgreSQL 16 on localhost:5432. App DB `postgresql+asyncpg://cardiosense:cardiosense@localhost:5432/cardiosense`,
  test DB `cardiosense_test` (same user). No SQLite anywhere.
- Ports: backend `8000`, frontend dev server `5173` (proxy `/v1` incl. WebSocket to `localhost:8000`).
- Model dir: env `CARDIOSENSE_MODEL_DIR`, default `<repo>/ml/models/cardiosense_beat_model`
  (`beat_model_ts.pt` TorchScript ensemble `(beat[B,2,250], rr[B,7]) -> probs[B,4]`, and `model_config.json`).
  If the model files are missing the backend must still run in **rules-only mode** (all beats `Unknown`,
  banner flag `model_loaded=false`).
- The repo is not a git repo; several agents work in it at once. Only touch your own directories.

## 2. Wire formats

### 2.1 Device/replay → server: `WS /v1/stream/{device_id}?key=<device_key>`

All messages are JSON text frames.

```jsonc
// first message, once per connection
{"type":"hello","device_id":"dev-demo-208","fs":360,"firmware":"sender-1.0","source":"replay", // "serial" | "replay"
 "adc":{"zero":1024,"gain":200.0,"units":"adu"},   // mV = (sample - zero)/gain ; omit => samples are already mV
 "record":"208","dataset":"MIT-BIH",               // replay only
 "speed":1.0}
// then, ~every 100 ms
{"type":"frame","seq":17,"device_id":"dev-demo-208","t0":6120,   // t0 = index of first sample since stream start
 "fs":360,"samples":[1003,1004,...],"lead_off":false,"source":"replay",
 "annotations":[{"sample":6209,"symbol":"V"}]}   // optional, replay ground truth, absolute sample index, raw MIT symbol
{"type":"bye"}                                   // optional clean end
```

Server replies: `{"type":"ack","session_id":"<uuid>","model_loaded":true}` after hello;
`{"type":"error","detail":"..."}` then close on auth/consent failure. Server rules: duplicate `seq` dropped;
a jump in `t0` is a **gap** (recorded, never interpolated); a new connection for the same device ends the old
session and starts a new one. One session per connection.

### 2.2 Server → UI: `WS /v1/live/{patient_id}?token=<access_jwt>`

Sample indices are absolute, in the **device sample rate**, since session start. `time_s = sample / fs`.

```jsonc
{"type":"session","session_id":"..","patient_id":"..","device_id":"..","fs":360,"source":"replay",
 "record":"208","dataset":"MIT-BIH","started_at":"ISO","model_version":"..","rule_version":"rules-1.0.0",
 "model_loaded":true,"active":true}                       // on connect and whenever a session starts/ends
{"type":"signal","t0":6120,"fs":360,"mv":[0.012,...],"lead_off":false}   // display signal in mV, causal-filtered
{"type":"beat","sample":6209,"time_s":17.247,"label":"V","confidence":0.97,
 "probs":{"N":0.01,"S":0.0,"V":0.97,"F":0.02},"model_version":"..","truth":"V"}   // truth: AAMI class of the
                                                           // matched replay annotation or null
{"type":"window","id":"..","start_s":10.0,"end_s":20.0,"hr_bpm":78.5,"findings":[Finding],
 "s_burden_pct":0.0,"v_burden_pct":12.5,"sqi":0.93,"sdnn_ms":41.0,"rmssd_ms":28.0,
 "beat_counts":{"N":10,"S":0,"V":2,"F":0,"Unknown":0},"rule_version":"rules-1.0.0",
 "risk":{"score":62,"tier":"orange","factors":[{"text":"V burden 12.5% over the last minute","points":25,"rule_id":"V_BURDEN"}]}}
{"type":"alert","alert":Alert}                              // new or updated alert
{"type":"quality","state":"ok"|"lead_off"|"low_sqi"|"gap","detail":"..","sqi":0.4}
{"type":"status","stream":"connected"|"disconnected","last_sample_time":"ISO"}
```

`Finding = {"rule_id":"V_COUPLET","label":"V couplet","severity":"amber","start_s":14.2,"end_s":15.0,"value":2.0,"detail":"2 consecutive V beats"}`

`Alert = {"id":"..","patient_id":"..","session_id":"..","rule_id":"V_RUN","tier":"orange","title":"Non-sustained V run",
"detail":"..","state":"open"|"acknowledged"|"escalated"|"closed","created_at":"ISO","updated_at":"ISO",
"acknowledged_by":null,"note":null,"evidence":{"start_s":..,"end_s":..,"fs":250,"strip_mv":[...],
"beats":[Beat...],"window":{...},"rule_version":"..","model_version":".."}}`

Tiers: `green | amber | orange | red`. Beat labels: `N | S | V | F | Unknown`.
Rule ids: `BRADY, BRADY_SEVERE, TACHY, TACHY_SEVERE, PAUSE, PAUSE_CRITICAL, V_COUPLET, V_RUN, V_SUSTAINED,
BIGEMINY, TRIGEMINY, S_RUN, S_BURDEN, V_BURDEN, IRREGULAR` (+ device tasks `LEAD_OFF, LOW_SQI`, never clinical alerts).

## 3. Pipeline core API (`backend/cardiosense/core`) — pure Python, no DB/web

```python
from cardiosense.core import StreamProcessor, load_classifier, RULE_VERSION

clf = load_classifier(model_dir)            # returns None if files are missing (rules-only mode)
sp = StreamProcessor(fs=360, classifier=clf, adc_zero=1024, adc_gain=200.0, rules_config=None)
out = sp.push(samples: list[float] | np.ndarray, t0: int, lead_off: bool = False,
              annotations: list[dict] | None = None)      # call once per frame; cheap; thread-safe not required
out = sp.flush()                                           # at end of stream: emit what can still be emitted
# out: PipelineOutput dataclass, every field a plain JSON-serialisable dict/list in the §2.2 shapes
#   .signal   dict | None      {"t0","fs","mv","lead_off"}
#   .beats    list[dict]       finalised beats (each emitted exactly once, in order), incl. "truth"
#   .windows  list[dict]       rhythm windows (one per 10 s of signal) incl. "risk" (no "id")
#   .alerts   list[dict]       alert *candidates*: {"rule_id","tier","title","detail","evidence":{...}} — already
#                              confirmed (2-window rule), deduplicated and cooled down per BRD §11
#   .quality  list[dict]       quality state changes
#   .gaps     list[dict]       {"at_sample","missing"}
sp.model_version, sp.rule_version, sp.stats()  # stats: beats, per-label counts, latency numbers
```

Requirements: beat labels must match the offline `BeatClassifier.classify` path of `ml/cardiosense_beats.py`
(same preprocessing, R-peak detector, template, RR features, class bias, unknown threshold) as closely as a
streaming implementation allows; a beat is emitted about one beat after its R-peak (BRD "beat latency");
`push` must stay well under real time (budget: < 250 ms per call worst case, typically a few ms; do heavy work at
most ~2×/s). CPU-heavy work must be callable from `asyncio.to_thread`.

## 4. REST API (prefix `/v1`, JSON, Bearer access JWT; errors `{"detail": "..."}`)

| Method | Path | Notes |
|---|---|---|
| POST | `/auth/register` `/auth/login` `/auth/refresh` `/auth/logout` | login → `{access_token, refresh_token, token_type:"bearer", user}`; Argon2id; rotating refresh |
| GET | `/auth/me` | `User = {id,email,name,role,org_id,mfa_enabled}`; roles `patient,technician,clinician,org_admin,system_admin` |
| POST | `/auth/mfa/enrol` `/auth/mfa/verify` | TOTP (pyotp); login takes optional `otp` |
| GET/POST | `/patients` ; GET/PATCH `/patients/{id}` | list sorted by risk; `Patient = {id,name,mrn,dob,sex,notes,consent,is_demo,risk_score,risk_tier,last_event,signal_status,active_session_id,device_id,open_alerts}` |
| POST | `/patients/{id}/notes` ; GET same | clinical notes / symptoms |
| GET/POST | `/devices` ; POST `/devices/pair` | pair returns `{device_id, device_key}`; pairing code 10-min expiry |
| GET | `/patients/{id}/sessions` ; GET `/sessions/{id}` | session metadata + summary stats |
| GET | `/sessions/{id}/beats?from=&to=` ; `/sessions/{id}/events` ; `/sessions/{id}/signal?from=&to=` | seconds; signal returns `{fs,t0,mv[]}` (stored display signal) for replay/episode strips |
| GET | `/patients/{id}/trends?range=24h\|7d\|30d\|90d` | buckets of `{t,hr_bpm,sdnn_ms,rmssd_ms,s_burden_pct,v_burden_pct,risk_score,findings}` + gaps |
| GET | `/alerts?state=&tier=&patient_id=` ; GET `/alerts/{id}` | queue, red first |
| POST | `/alerts/{id}/ack` `/escalate` `/close` | body `{note}`; audited; no auto-close |
| POST | `/copilot/query` | `{patient_id, question?}` → `{id,text,citations:[{id,kind,label,time}],generated_by:"llm"\|"template",model,generated_at,disclaimer}` |
| GET | `/patients/{id}/insights` | stored insights |
| GET | `/reports/{patient_id}.pdf?range=` ; `/sessions/{id}/export.csv` | disclaimer, model + rule versions |
| POST | `/demo/replay` ; GET `/demo/records` ; POST `/demo/stop` | server spawns the sender for `{record, dataset, speed, patient_id?}` against a demo patient/device |
| GET | `/audit?actor=&from=&to=&action=` | org_admin/system_admin |
| GET/PATCH | `/admin/users` `/admin/users/{id}` ; GET/PUT `/admin/rules` | rule thresholds versioned |
| GET | `/health` ; `/meta` | meta: model_version, rule_version, model_loaded, model metrics/limitations from model_config.json, disclaimer |

Seed (idempotent, on startup when `CARDIOSENSE_SEED=1`, default on in dev): org "CardioSense Demo Clinic";
users `admin@cardiosense.dev`, `clinician@cardiosense.dev`, `tech@cardiosense.dev`, `patient@cardiosense.dev`
(password `CardioSense!23`); three demo patients (stable → record 100, watch → 223, critical → 208), each with a
paired device `dev-demo-<record>` and device key `demo-key-<record>`; demo patients are flagged `is_demo`.

## 5. Safety rules that every layer must respect (BRD §8, §11)

- Never diagnostic language. "Irregular rhythm — review", never "AFib". Disclaimer on every screen/report:
  "Research and demonstration system — not a medical device. Not an emergency service."
- Low SQI / lead-off suppress clinical alerts and raise a device task instead.
- F and Unknown beats never trigger an alert alone; S-only findings never exceed Amber.
- Orange/Red need the finding in 2 consecutive windows, except pause > 3 s (fires at once).
- Alerts are never auto-closed. Every alert stores an evidence snapshot, rule id + version, model version.
- Replay/demo data always carries a visible "DEMO / REPLAY" badge and is never mixed with real patient records.
- Never colour alone (label/pattern too). Design tokens: Ink #0B1B2B, Navy #0E2A47, Teal #00A896, Coral #E0484D,
  Amber #C9820A; dark theme default.
