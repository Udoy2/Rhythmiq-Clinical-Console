# CardioSense AI — Business Requirements Document v1.1

Continuous ECG monitoring, beat-level arrhythmia detection and history-grounded clinical intelligence.

| Field | Detail |
|---|---|
| Document ID | CSAI-BRD-001 |
| Version | 1.1 — rewritten around the CardioSense beat model |
| Date | November 2026 |
| Programme | Healthcare AI Hackathon |
| Status | Research and demonstration system — **not a medical device** |
| Supersedes | `CardioSense_AI_BRD.pdf` (v1.0) |
| Model artefacts | `cardiosense_beat_model/` (weights, config, metrics, training notebook) |

**Number labels used throughout:** **Measured** = from our training and test logs in `cardiosense_beat_model/`. **Target** = a goal not yet measured. **Illustrative** = a planning assumption.

---

## What changed from v1.0

| Area | v1.0 | v1.1 |
|---|---|---|
| Beat model | Third-party 1D-CNN, 99.83% validation accuracy | Our own 1D ResNet ensemble, evaluated on unseen patients and an external hospital dataset |
| Rhythm model | HybridECGNet (AlexNet + ViT on CWT scalograms), ARR / CHF / NSR | Deterministic **rhythm rules engine** built on our beat labels and RR intervals |
| YOLOv8 baseline | Listed as fallback | Removed (third-party, not needed) |
| Headline metrics | 95.56% / 0.997 AUROC on 135 scalograms | V sensitivity 0.92 on unseen patients; INCART macro-F1 0.62; beat detection 0.98 / 0.999 |
| Scalogram | "The model's actual input" | Visual aid only; not used by any model |
| AFib alerts | Model confidence > 0.80 | Rule-based "irregular rhythm — review" flag; not validated as an AFib detector |
| 60 Hz prototype | Fallback with warning | Validated fallback: macro-F1 0.539 vs 0.542 at 250 Hz |
| Demo stream | Synthetic | Replay of public, cardiologist-labelled recordings (MIT-BIH, INCART) |
| Data model | RhythmWindow (ARR/CHF/NSR) | Adds **Beat** entity; RhythmWindow holds rule outputs |

---

## Executive summary

Intermittent arrhythmias are missed because monitoring is episodic, expensive and retrospective. CardioSense AI turns a low-cost AD8232 sensor kit into a live clinical workspace. The web app receives the ECG stream, detects every heartbeat, classifies it, tracks rhythm patterns, and escalates when something changes. A copilot explains what changed using the patient's own history, always with citations and never with a diagnosis.

| Headline | Value | Status |
|---|---|---|
| Ventricular (V) beat sensitivity, unseen patients (MIT-BIH DS2) | **0.92** | Measured |
| Macro-F1, external hospital dataset (INCART, 75 recordings) | **0.62** | Measured |
| Heartbeat detection sensitivity / positive predictivity | **0.98 / 0.999** | Measured |
| Accuracy on the standard beat-wise split (for comparison with literature) | **99.3%** | Measured |
| Inference speed, one CPU core | ~180× real time | Measured |
| Acquisition-to-alert latency (p95) | < 3 s | Target |
| Prototype bill of materials | ~$22 | Illustrative |

### What makes it different

- **Honest, patient-wise AI.** The model is evaluated on patients it never saw and on a second hospital's data. We report those numbers first, not the inflated beat-wise ones.
- **Two timescales.** A neural beat classifier labels every heartbeat (N, S, V, F). A deterministic rules engine turns those labels and RR intervals into rhythm findings: rate, pauses, ectopic runs, burden, irregularity.
- **Patient-personalised beats.** Each beat is compared with a running template of the same patient's recent beats, so the model judges "different from this person's normal", not just "different from the textbook".
- **Uncertainty is visible.** Low-confidence beats are shown as "Unknown" instead of guessed.
- **Grounded copilot.** It cites the events it uses and cannot diagnose or prescribe.

### What the release must prove

A working web app with sign-in, live streaming from the sensor (or a labelled replay), server-side beat classification and rhythm rules, a monitoring console, history, an alert engine and the copilot. It must stay under 3 s p95 latency and pass a retrospective replay test on recordings the model never trained on.

---

## 1. Problem and opportunity

| Problem | Effect |
|---|---|
| Capture is episodic and expensive | Holter studies last 24–48 h, cost hundreds of dollars, and report days later |
| Data is rich but insight-poor | Clinicians read raw strips or spreadsheets; no live view, trend or alerting |
| Consumer devices stop at the wrist | Spot checks, closed ecosystems, no clinical queue |

**Problem statement:** clinicians and at-risk patients lack an affordable way to continuously capture ECG, see rhythm changes as they happen, and understand what changed and why.

**Why now:** commodity ECG front-ends (AD8232, under $10); small neural networks that run on a CPU; retrieval-augmented language models that can summarise a patient's own record with citations; clinics already accept remote-monitoring workflows.

| Stakeholder | Pain today | Success |
|---|---|---|
| Patient with palpitations | Symptoms intermittent, tests normal | A small kit and a clear status to share |
| Cardiologist | Late, tedious reports, no prioritisation | A risk-sorted queue with evidence and a cited summary |
| Clinic | Holter hardware is capital-heavy | Low-cost kit plus a subscription console |
| Health system / insurer | Admissions from undetected deterioration | Earlier detection, auditable outcomes |

---

## 2. Vision, objectives and success metrics

**Vision:** every heartbeat visible, understandable and actionable, at a cost a community clinic can afford. CardioSense AI is a screening and monitoring layer, not a replacement for a cardiologist.

| ID | Objective | Target | Measured by |
|---|---|---|---|
| O1 | End-to-end path on physical hardware | Sensor → server → model → alert < 3 s p95 | Trace IDs across the pipeline |
| O2 | Clinically meaningful beat detection on unseen patients | V sensitivity ≥ 0.90; beat detection sensitivity ≥ 0.97 (**measured: 0.92 and 0.98**) | DS2 and INCART test sets; replay set |
| O3 | Output understandable in under a minute | SUS ≥ 80; task completion ≥ 90% | 5 usability sessions |
| O4 | Copilot grounded and safe | 100% of claims cite an event; 0 diagnostic claims | Golden-question audit (n = 100) |
| O5 | Runs as a service | 50 concurrent streams; 99.5% uptime in a 4-hour soak | Load test and soak log |

| Metric | Baseline | Target | Instrumentation |
|---|---|---|---|
| Alert latency (p95) | Not measured | < 3.0 s | OpenTelemetry spans |
| V sensitivity on replay | 0.92 (DS2, measured) | ≥ 0.90 | Confusion matrix |
| False alert rate | Not measured | < 1 per 24 h per patient | Alert audit log, replay |
| Signal quality acceptance | Not measured | ≥ 95% of streaming minutes | SQI pipeline |
| UI response | — | < 100 ms | Web performance marks |
| Clinician triage time | Manual review | −50% | Timed task study |

**Non-goals:** diagnosis or treatment; 12-lead interpretation or ST-elevation triage; emergency dispatch; regulated clinical use; native mobile apps; hospital EHR integration.

---

## 3. Product overview

| Surface | Primary user | Core job | Key components |
|---|---|---|---|
| Live console | Clinician, monitoring nurse | See the rhythm now and decide whether to act | Waveform with beat labels, beat ribbon, rhythm status, risk gauge, vitals, AI insight |
| History and trends | Clinician, patient | New, worse or stable? | Episode timeline, ectopic burden, HR/HRV trend, replay |
| AI copilot | Clinician | Explain the change with evidence | Cited summary, comparison with prior episodes |

| Persona | Job to be done | Success signal |
|---|---|---|
| Rahim, 54, palpitations | "Tell me whether what I felt is dangerous, and show my doctor." | Understands status without calling emergency |
| Dr. Nusrat, cardiologist | "Put the risky ones on top and show me why." | Triages in < 60 s per patient |
| Farhana, clinic technician | "Tell me the signal is good before the patient leaves." | < 5 min setup, < 2% refits |
| Admin / auditor | "Show me who saw what, and when." | Complete audit trail |

---

## 4. Scope and release plan

| In scope — MVP | Out of scope — later |
|---|---|
| Email/password auth, roles, invites, MFA for clinicians | SSO / SAML |
| Live ingestion at 250 Hz (60 Hz fallback) over WebSocket | Cellular fallback, multi-region |
| Live ECG with beat labels, beat ribbon, rhythm status, risk gauge | 12-lead view, full measurement suite |
| Beat model (server-side) + rhythm rules engine | On-device inference |
| **Replay mode** from public labelled recordings | Synthetic ECG generation |
| History, trends, episode replay, CSV/PDF export | EHR integration (HL7/FHIR) |
| Four-tier alert engine, in-app and email delivery | SMS / voice escalation, on-call rosters |
| Grounded copilot with citations and guardrails | Patient-facing chat agent |
| Admin console, audit log, device status | White labelling |
| CWT scalogram as a display panel (SHOULD) | Scalogram-based models |
| — | AFib-specific model (see §8.5 roadmap) |

| Phase | Audience | Exit criteria |
|---|---|---|
| R1 — Demo | Judges, clinical advisors | Live hardware demo, replay validation, 50-stream soak, safety review |
| R2 — Pilot | 2–3 clinics, 30–50 patients | Ethics approval, consent, prospective recordings, AD8232 validation |
| R3 — Regulated | Health systems | QMS, ISO 13485, clinical evidence, regulator pathway |

**Assumptions and constraints**

- Single lead (lead II / MLII). Enough for rhythm and ectopy, not for ischaemia. The product must never imply otherwise.
- 250 Hz is the target sample rate. The 60 Hz prototype rate is a validated fallback (§8.3) but still shows a quality notice.
- Noisy environment is normal. Signal-quality gating is a requirement.
- Web-first, responsive; desktop for clinicians, phones for patients.
- Evidence, not diagnosis: every claim is framed as decision support.

---

## 5. System architecture and data flow

The live path never waits for a database write or a language model.

| # | Step | What happens | Failure behaviour |
|---|---|---|---|
| 1 | Acquire | AD8232 amplifies lead II; the MCU samples at 250 Hz and reads lead-off pins | Local buffer |
| 2 | Frame | Samples batched into ~100 ms frames with sequence number, device ID, lead-off flag | Duplicate sequence numbers dropped |
| 3 | Transport | USB serial → local bridge → WebSocket (demo); BLE later | Reconnect with backoff |
| 4 | Fan-out | Frames go to the live UI and the inference worker | Live preview may drop; persistence never does |
| 5 | Persist | Raw frames, beats, rhythm windows and alerts stored | Write queue; alerting continues |
| 6 | Infer | Filter → R-peak detection → beat model → rhythm rules; results carry the model version | Rules-only mode with a banner |
| 7 | Decide | Risk engine merges beat labels, rhythm findings, trends and SQI into a score and tier | No downgrade without evidence |
| 8 | Notify | Alert engine deduplicates, applies cooldowns, pushes to console and copilot | Retry; audit every attempt |

**Signal source abstraction.** The backend reads from a `SignalSource`. Two implementations share one frame format: `SerialSource` (the AD8232 kit) and `ReplaySource` (MIT-BIH / INCART records streamed at real-time speed, scaled to the device's 10-bit range). The rest of the system cannot tell them apart, so the app can be built and demonstrated before the hardware is finished.

**Beat latency.** Each beat label is available about one beat after its R-peak, because the model uses the following RR interval.

| Layer | Choice | Why |
|---|---|---|
| Web app | Next.js + TypeScript + Tailwind + shadcn/ui | Fast to build, accessible components |
| Waveform | Canvas / WebGL | 60 fps at 250 Hz |
| API | FastAPI + Pydantic | Async, typed contracts, OpenAPI |
| Streaming | WebSocket (+ Redis Streams when scaling) | Low latency, simple |
| Database | PostgreSQL (TimescaleDB optional) | Relational records plus time series |
| Inference | PyTorch on CPU (TorchScript or `.pth`); ONNX available | ~180× real time per core |
| Auth | Auth.js or Clerk with organisation roles | MFA and invites without building identity |
| LLM | Hosted model behind a provider-agnostic adapter | Swap models; template fallback |
| Observability | OpenTelemetry + Prometheus + Grafana | Trace the 3 s budget |

---

## 6. Hardware requirements

| Requirement | R1 target | Rationale |
|---|---|---|
| Front-end | SparkFun AD8232, 3 electrodes, lead II | Low cost, single lead |
| MCU | Arduino Uno / Nano (optional JDY-08 BLE module) | Available kit |
| Sampling rate | 250 Hz (60 Hz validated fallback) | Matches model input |
| Resolution | 10-bit ADC (12-bit preferred later) | Enough for 0.5–5 mV after AD8232 gain |
| Lead-off detection | LO+ / LO− on D10 / D11, sent in every frame | Drives the "electrodes disconnected" state |
| Filtering | Raw samples sent; 0.5–40 Hz filtering done on the server | One filter definition, identical to training |
| Connectivity | USB serial for the demo; BLE for the pilot | Reliability first |
| Timestamping | Sample counter on device + server clock correction | Latency measurement and replay |
| Safety | Battery or laptop-on-battery power; no mains path to the patient | Patient safety and lower noise |

| Bill of materials item | Qty | Unit (illustrative) |
|---|---|---|
| AD8232 module | 1 | $8.00 |
| Arduino Nano | 1 | $5.00 |
| Ag/AgCl electrodes (pack) | 1 | $2.00 |
| 3-lead cable | 1 | $3.50 |
| Enclosure, strap, misc. | 1 | $3.50 |
| **Total** | | **~$22** |

**Firmware requirements**

- Fixed-rate sampling from a timer, not `delay()`.
- Frame format: start marker, sequence number, lead-off flags, N samples, checksum.
- Never interpolate missing samples; the server marks gaps explicitly.
- Report firmware version and sample rate on connect.

---

## 7. Software requirements

### 7.1 Functional requirements

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-01 | Register, verify email, log in, log out | MUST | Argon2id; short-lived access token + rotating refresh; logout revokes session |
| FR-02 | TOTP MFA for clinicians; admins can enforce it | MUST | Enrolment, recovery codes, per-org enforcement |
| FR-03 | Password reset by single-use email token | MUST | 30 min expiry; old sessions revoked |
| FR-04 | Roles: patient, technician, clinician, org admin, system admin | MUST | Server-side authorisation on every endpoint |
| FR-05 | Patient profiles, consent, clinical notes | MUST | Consent required before streaming; edits audited |
| FR-06 | Pair a device to a patient; show signal quality and firmware | MUST | Pairing code expires in 10 min |
| FR-07 | Ingest frames at 250 Hz (or 60 Hz) with ordering and gap detection | MUST | No duplicates; gaps marked; p95 receive < 500 ms |
| FR-08 | Live ECG at 25 mm/s, 10 mm/mV on a calibrated grid | MUST | 60 fps; pause, scroll, zoom |
| FR-09 | Overlay beat markers with labels N, S, V, F or Unknown | MUST | Marker within one sample of the R-peak; tooltip shows confidence, probabilities and model version |
| FR-10 | Beat ribbon of the last 60 s | MUST | One block per beat; colour plus pattern |
| FR-11 | Rhythm window every 10 s from the rules engine | MUST | Stores HR, rhythm findings, burden, SQI, rule version |
| FR-12 | 0–100 risk score with contributing factors | MUST | Reproducible from stored inputs; factors in plain language |
| FR-13 | Cited AI insight from history and current episode | MUST | Every claim links to an event ID; no diagnostic language |
| FR-14 | Clinician follow-up questions to the copilot | SHOULD | Restricted to retrieved records |
| FR-15 | Tiered alerts with deduplication and cooldown | MUST | Every alert has a rule ID and evidence snapshot |
| FR-16 | Alerts in-app and by email; webhook | MUST | Delivery logged; retry with backoff; acknowledgement captured |
| FR-17 | History timeline and trends (HR, HRV, S/V burden, rhythm findings) | MUST | 7/30/90-day ranges; gaps visible |
| FR-18 | Replay any session with the same waveform, labels and alerts | MUST | Deterministic; speed control |
| FR-19 | PDF report and CSV export | MUST | Disclaimer, model and rule versions, evidence |
| FR-20 | Clinician review: acknowledge, annotate, escalate, close | SHOULD | Transitions audited |
| FR-21 | Admin console: users, roles, devices, alert rules, audit search | MUST | Org admins only; changes versioned |
| FR-22 | Demo mode streaming a public labelled recording | MUST | Visible "DEMO / REPLAY" badge; ground-truth labels shown alongside model labels; never mixed with patient records |
| FR-23 | Live CWT scalogram panel (0.8–38 Hz, 1 s refresh) | SHOULD | Labelled as a visual aid, not model input |

### 7.2 Non-functional requirements

| ID | Area | Target |
|---|---|---|
| NFR-01 | Latency | < 3.0 s acquisition-to-alert p95; < 250 ms inference per frame batch |
| NFR-02 | Throughput | 50 concurrent streams on one deployment |
| NFR-03 | Availability | 99.5% during the pilot |
| NFR-04 | Durability | RPO ≤ 5 min; RTO ≤ 1 h |
| NFR-05 | Security | OWASP ASVS L2; dependency and secret scanning |
| NFR-06 | Privacy | HIPAA- and GDPR-aligned controls; local residency |
| NFR-07 | Accuracy | V sensitivity ≥ 0.90 and beat detection sensitivity ≥ 0.97 on unseen-patient replay |
| NFR-08 | Safety | False alerts < 1 per 24 h per patient |
| NFR-09 | Usability | ≥ 90% unaided task completion; SUS ≥ 80 |
| NFR-10 | Accessibility | WCAG 2.1 AA; colour-blind-safe alert encoding |
| NFR-11 | Compatibility | Last 2 versions of Chrome, Edge, Safari; ≥ 768 px |
| NFR-12 | Observability | Every inference traceable by request ID and model version |
| NFR-13 | Maintainability | ≥ 70% backend coverage on critical paths |
| NFR-14 | Portability | Containerised, one-command local stack |

### 7.3 Roles and permissions

| Capability | Patient | Technician | Clinician | Org admin | System admin |
|---|---|---|---|---|---|
| View own live ECG | Yes | Yes | Yes | Yes | No |
| View org patients | No | Assigned | Assigned | Yes | No |
| Acknowledge / escalate alerts | No | No | Yes | Yes | No |
| Annotate episodes | No | No | Yes | Yes | No |
| Manage users and roles | No | No | No | Yes | Yes |
| Manage devices | Own | Yes | No | Yes | Yes |
| View audit log | Own | No | Own | Yes | Yes |
| Configure alert rules | No | No | Suggest | Yes | Yes |

---

## 8. AI and model requirements

### 8.1 Model portfolio

| Component | Input | Output | Evidence | Role |
|---|---|---|---|---|
| R-peak detector (Pan-Tompkins style) | Filtered ECG at 250 Hz | Beat positions | Sensitivity 0.98, positive predictivity 0.999 (measured) | Finds every heartbeat |
| **Beat classifier** (1D ResNet ensemble) | 1 s beat window + difference from patient template + 7 RR features | N / S / V / F probabilities; "Unknown" below 0.5 | See §8.3 (measured) | Beat labels, ectopy burden |
| **Rhythm rules engine** | Beat labels, RR intervals, SQI | Rate, pauses, ectopic patterns, burden, irregularity | Deterministic, unit-tested; replay-validated (target) | Rhythm status, alert triggers |
| Signal quality (SQI) | Lead-off flags, saturation, noise, Unknown-beat ratio | 0–1 score per window | Rule-based | Gates every alert |
| Copilot (LLM + retrieval) | Structured events + guideline snippets | Cited insight ≤ 120 words | Golden set: factuality ≥ 95% (target) | Explanation, never diagnosis |

**Classes (AAMI standard):** N = normal and bundle-branch beats; S = supraventricular ectopic; V = ventricular ectopic; F = fusion of normal and ventricular.

### 8.2 Beat classifier: how it was built

| Item | Detail |
|---|---|
| Training data | MIT-BIH Arrhythmia Database, 44 patients (4 paced records excluded) |
| External data | St Petersburg INCART, 75 recordings, 257 Hz, different hospital and equipment |
| Noise data | MIT-BIH Noise Stress Test Database (electrode motion, muscle, baseline wander) |
| Preprocessing | Resample to 250 Hz; Butterworth 0.5–40 Hz band-pass (order 3, zero-phase) |
| Beat window | 360 ms before to 640 ms after each R-peak (250 samples) |
| Patient template | Running median of that patient's previous 20 beats; model sees the beat and beat minus template |
| RR features | 7 timing features: previous and next RR, local averages over 10 and 100 beats, and their ratios |
| Architecture | 1D ResNet (6 residual blocks, ~794k parameters) with an RR branch; 3 copies averaged |
| Imbalance | Softened class weights (power 0.5), label smoothing 0.05; no oversampling |
| Training | AdamW, lr 0.001, warmup + cosine, 15 epochs, weight averaging over the last 5 (SWA) |
| Augmentation | Real NSTDB noise at 6–30 dB SNR; ±32 ms beat-position jitter |
| Model selection | Highest 5-fold patient-wise cross-validated macro-F1 on DS1; rejected CNN-BiLSTM and LightGBM |
| Deployment model | Retrained on all MIT-BIH + INCART data; version `cardiosense-beat-resnet-tpl-20261117` |
| Files | `beat_model.pth`, `beat_model_ts.pt` (TorchScript), `beat_model.onnx`, `cardiosense_beats.py`, `model_config.json` |

### 8.3 Measured performance

All tests are **patient-wise**: no patient appears in both training and test. The deployment model is retrained on all data, so these numbers come from the evaluation models trained on DS1 only.

| Test set | Accuracy | Macro-F1 | N Se | S Se | V Se | V +P | F Se |
|---|---|---|---|---|---|---|---|
| 5-fold cross-validation, DS1 (22 patients) | 0.920 | 0.560 | 0.94 | 0.40 | 0.87 | 0.64 | 0.25 |
| DS2, 22 unseen patients (3-model ensemble) | 0.926 | 0.547 | 0.96 | 0.20 | 0.92 | 0.77 | 0.17 |
| INCART, external hospital (3-model ensemble) | 0.949 | 0.619 | 0.96 | 0.91 | 0.90 | 0.85 | 0.45 |
| Beat-wise 70/30 split (for literature comparison) | 0.993 | 0.936 | 0.99 | 0.97 | 0.98 | 0.99 | 0.94 |

*Se = sensitivity (recall); +P = positive predictivity (precision).*

**End-to-end and stress tests (DS2, own R-peak detector):**

| Condition | Beat detection Se / +P | Macro-F1 | V Se |
|---|---|---|---|
| Cardiologist R-peaks (reference) | 1.00 / 1.00 | 0.547 | 0.92 |
| Own detector, 250 Hz | 0.980 / 0.999 | 0.542 | 0.90 |
| Own detector, 125 Hz input | 0.980 / 0.999 | 0.542 | 0.90 |
| Own detector, 60 Hz input (prototype rate) | 0.980 / 0.999 | 0.539 | 0.91 |
| Added noise, 18 dB SNR | 0.980 / 0.999 | 0.540 | 0.90 |
| Added noise, 12 dB SNR | 0.980 / 0.999 | 0.532 | 0.90 |
| Added noise, 6 dB SNR | 0.979 / 0.999 | 0.517 | 0.90 |

**"Unknown" threshold (DS2):**

| Threshold | Beats labelled | Accuracy of labelled beats |
|---|---|---|
| 0.4 | 98.3% | 93.6% |
| **0.5 (default)** | **94.4%** | **95.5%** |
| 0.6 | 89.2% | 97.1% |
| 0.7 | 80.8% | 98.1% |

### Why we do not headline a 99% number

Many published MIT-BIH results use a beat-wise split, where beats from the same patient appear in both training and test. Some also oversample before splitting, so copies of the same beat land on both sides. Under the beat-wise split our model also reaches 99.3%. The patient-wise numbers above are what a new patient would actually experience.

### Known limitations and how the product handles them

| Limitation | Evidence | Product handling |
|---|---|---|
| S beats missed when they dominate the rhythm | DS2 S Se 0.20; about 0.59 without record 232, where 78% of beats are S | S findings never escalate above Amber alone; rate and pause rules cover these rhythms |
| F beats have very low precision on new data | INCART F +P 0.04 | F never triggers an alert alone; shown as a label only |
| Not yet validated on AD8232 recordings | No AD8232 data yet | Record team members during R1; R2 pilot validation |
| Single lead | — | No ischaemia or 12-lead claims |
| No AFib-specific model | — | Rule-based "irregular rhythm — review" flag only (§8.4) |

### 8.4 Rhythm rules engine

Runs every 10 s over the latest beats. Unknown beats are excluded from burden and pattern counts and lower the SQI.

| Finding | Rule (R1 defaults, configurable by org admin) |
|---|---|
| Heart rate | Median RR over the last 10 s, N and S beats only |
| Bradycardia | HR < 50 bpm for ≥ 10 s (severe < 40) |
| Tachycardia | HR > 100 bpm at rest for ≥ 10 s (severe > 150) |
| Pause | RR interval > 2.0 s (critical > 3.0 s) |
| V couplet | 2 consecutive V beats |
| V run (non-sustained) | ≥ 3 consecutive V beats, < 30 s |
| Sustained V pattern | V run ≥ 30 s at > 100 bpm |
| Bigeminy / trigeminy | V every 2nd / 3rd beat for ≥ 6 cycles |
| S run | ≥ 3 consecutive S beats at > 100 bpm |
| Ectopic burden | % of S and % of V beats over 1 min, 1 h and 24 h |
| Irregular rhythm | High RR irregularity over 60 s with ectopic beats excluded, e.g. RMSSD / mean RR above threshold. Label: "Irregular rhythm — review". Not an AFib diagnosis |
| HRV | SDNN and RMSSD over normal-to-normal intervals, 5 min windows |

### 8.5 Validation, calibration and roadmap

| Stage | Method | Status |
|---|---|---|
| Patient-wise cross-validation | 5-fold on DS1 | Done |
| Unseen-patient test | MIT-BIH DS2 | Done |
| External validation | INCART (second hospital) | Done |
| Robustness | Sample rate 60–250 Hz; noise 6–18 dB SNR | Done |
| Replay validation of rules and alerts | DS2 + INCART replay; false alerts per 24 h | R1 target |
| AD8232 validation | Team recordings with manual review | R1 target |
| Calibration | Temperature scaling; reliability diagram; ECE ≤ 0.05 | R1 target |
| Irregular-rhythm rule check | MIT-BIH Atrial Fibrillation Database | R1 stretch |
| AFib model | Train and evaluate patient-wise on AF databases | R2 |
| Prospective pilot | Clinic recordings with cardiologist labels | R2 |
| Drift monitoring | Input/output distribution shift, weekly | R2 |
| Governance | Model card, version pinning; every result shows its model version | R1 |

### 8.6 Copilot: grounded and bounded

| Part | Behaviour |
|---|---|
| Context builder | Current episode, last 24 h of beats and rhythm findings, 14-day trends, symptoms, device quality, similar prior episodes |
| Retriever | Patient event records and versioned guideline snippets, each with an ID and date |
| Generator | ≤ 120 words: what changed, comparison with baseline, what to check, confidence qualifier |
| Citation enforcer | Sentences without a retrieved ID are removed before display |
| Safety classifier | Blocks diagnostic, prescriptive and emergency-instruction output |
| Fallback | Rules-based template with the same citations if the LLM is slow or down |

> System prompt extract: "You are a clinical documentation assistant. Use only the supplied evidence. Do not diagnose, prescribe, or give emergency instructions. If the evidence is insufficient, say so. Every factual sentence must cite an evidence ID in square brackets. Maximum 120 words."

---

## 9. Data, privacy and security

| Data class | Examples | Sensitivity | Retention (R1) |
|---|---|---|---|
| Identity | Name, email, date of birth | High | Account lifetime + 30 days |
| Clinical profile | Conditions, medications, symptoms, consent | High | Account lifetime + 30 days |
| Raw ECG | 250 Hz frames | Very high | 90 days hot; 12 months archive |
| Derived events | Beats, rhythm windows, risk scores, alerts | High | 24 months |
| Audit | Who accessed what, when | Medium | 7 years |
| Telemetry | Latency, errors, device health (no signal) | Low | 13 months |

**Controls:** consent before capture; least-privilege access; TLS in transit and encryption at rest; no ECG in logs; pseudonymised data for analytics; regional residency; breach runbook; no patient data used to train external foundation models.

**Public datasets:** MIT-BIH Arrhythmia, INCART and NSTDB (PhysioNet) are used for training, evaluation and demo replay, with attribution as their licences require. Replay data is never mixed with patient records.

---

## 10. User experience and interface

| Screen | Purpose | Must-have components |
|---|---|---|
| Sign in / register | Get in safely | Email, password, MFA, invite, reset |
| Patient list | Choose who to look at, sorted by risk | Search, risk badge, last event, signal status |
| Live console | Judge the current rhythm | Waveform with beat labels, beat ribbon, rhythm status, risk gauge, vitals, insight |
| Episode detail | Understand one event | Snapshot, beat probabilities, rule that fired, notes |
| History and trends | Is it changing? | Timeline, HR/HRV, S/V burden, date range |
| Alerts centre | Triage and close the loop | Queue, tier filters, acknowledge, escalate |
| AI insights | Read cited explanations | Insight cards, citation chips, follow-up input |
| Reports | Share a summary | PDF preview, date range, disclaimer, export |
| Admin / settings | Operate the service | Users, roles, devices, rules, audit search |

| Live element | Specification |
|---|---|
| ECG sweep | 25 mm/s, 10 mm/mV, calibrated grid, 6 s window |
| Beat markers | N / S / V / F / Unknown label at each R-peak; confidence on hover |
| Beat ribbon | Last 60 s, one block per beat, colour plus pattern |
| Rhythm status | HR, current findings (e.g. "V couplet", "Pause 2.4 s"), rule version |
| Risk gauge | 0–100 with tier band; never colour-only |
| Vitals strip | HR, HRV, S/V burden |
| Quality banner | Lead-off, motion, low SQI; suppresses clinical alerts |
| Replay badge | "DEMO / REPLAY — record 119 (MIT-BIH)", with ground-truth labels toggle |
| Scalogram panel (SHOULD) | 0.8–38 Hz, 1 s refresh, labelled "visual aid" |

| Design token | Value | Use |
|---|---|---|
| Ink | #0B1B2B | Text, dark surfaces |
| Navy | #0E2A47 | Headers, navigation |
| Teal | #00A896 | Normal rhythm, primary action |
| Coral | #E0484D | Critical, ventricular events |
| Amber | #C9820A | Watch tier, degraded quality |

Principles: WCAG 2.1 AA; never colour alone; dark theme default; waveform at 60 fps; a dropped stream keeps the last state visible with a "reconnecting" banner and the last sample time.

---

## 11. Alerts and clinical escalation

| Tier | Example triggers | Notification (R1) | Expected action |
|---|---|---|---|
| Green — Normal | HR 50–100, regular, V burden < 1%, good SQI | Status only | None |
| Amber — Watch | S or V burden 1–10% over 1 h; V couplets; bigeminy / trigeminy; S runs; HR 40–50 or 100–150 at rest; single irregular-rhythm window | In-app; daily digest | Review within 24 h |
| Orange — Urgent | V burden > 10%; non-sustained V run (≥ 3 beats); pause 2–3 s; irregular rhythm for ≥ 2 consecutive windows | In-app + email | Acknowledge within 30 min |
| Red — Critical | HR < 40 or > 150 for ≥ 10 s; pause > 3 s; sustained V pattern ≥ 30 s | In-app + email, top of queue | Immediate review; documented handoff |

**Alert rules**

- **Signal quality gates everything.** Low SQI or lead-off raises a device task, not a clinical alert.
- **F beats and Unknown beats never trigger an alert alone.** S beats alone never go above Amber.
- **Confirmation.** Orange and Red need the finding in 2 consecutive windows, except pauses > 3 s, which fire at once.
- **Deduplication and cooldown.** One alert per rule per episode; re-notify only on state change or after cooldown.
- **Evidence snapshot.** Each alert stores the 10 s strip, beat labels and probabilities, the rule ID and version, and the model version.
- **Human acknowledgement.** Alerts cannot be auto-closed; every decision is audited.
- **Safety language.** The app is not an emergency service and says so.

---

## 12. Business model and market

All values are illustrative.

| Tier | Who | Price | Includes |
|---|---|---|---|
| Kit | Clinic / patient | $39 one-time | Sensor, electrodes, enclosure, onboarding |
| Patient | Individual | $7 / month | Live view, history, reports, alerts to one clinician |
| Clinic | Small practice | $149 / month | Up to 100 patients, triage queue, exports |
| Hospital | Department | $2,400 / year | Unlimited seats in one department, audit, SLA |
| Analytics | Insurer / research | Custom | De-identified cohorts, outcome dashboards |

**Go-to-market:** design-partner clinics in Dhaka plus one telemedicine provider; a free 90-day pilot in exchange for labelled data and a case study; demo the triage queue, not the algorithm; publish measured alert precision and clinician time saved.

| Alternative | Limitation | CardioSense edge |
|---|---|---|
| Holter / event monitor | Episodic, capital-heavy, delayed report | Continuous, low-cost, live console |
| Smartwatch ECG | Spot checks, closed ecosystem | Continuous record + clinician workflow |
| KardiaMobile-style device | Patient-initiated only | Passive capture + trends + explanation |
| Remote monitoring vendors | Expensive, opaque algorithms | Transparent, patient-wise-validated AI, ~$22 kit |

---

## 13. Delivery plan, team and budget

| Milestone | Definition of done |
|---|---|
| M1 — App skeleton + replay | Auth, patient list, ReplaySource streaming a MIT-BIH record into the live console |
| M2 — Intelligence | Beat model and rules engine served; labels, ribbon, risk score, alerts |
| M3 — Copilot and history | Cited insights, trends, reports, alert workflow |
| M4 — Hardware | 250 Hz firmware, SerialSource, lead-off handling, AD8232 recordings reviewed |
| M5 — Demo ready | Replay validation, soak test, rehearsed script, fallback recording |

| Role | Owns | Demo deliverable |
|---|---|---|
| Technical lead / ML | Model serving, rules engine, validation | Live beat labels with visible confidence |
| Full-stack engineer | API, streaming, database, auth | Role-based login, stable streams |
| Frontend / visualisation | Console, charts, design system | 60 fps ECG with beat ribbon |
| Hardware engineer | Sensor, firmware, SQI | AD8232 kit streaming at 250 Hz |
| Product / clinical liaison | Scope, safety review, demo script | Clinical sign-off and narrative |

| Budget item | Cost (illustrative) |
|---|---|
| Cloud and database | $150 |
| Sensor kits (×4) | $150 |
| LLM API | $100 |
| Clinical advisor | $200 |
| Design assets | $50 |
| Contingency | $100 |
| **Total** | **$750** |

Model training used Kaggle's free GPU tier at no cost.

---

## 14. Risks and mitigations

| ID | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Motion artefact causes false alerts | High | High | SQI gating; 2-window confirmation; electrode placement guide |
| R2 | AD8232 signal differs from training data | Medium | High | Noise augmentation in training; early team recordings; replay fallback for the demo |
| R3 | Over-claiming accuracy | Medium | High | Patient-wise numbers first; limitations shown in the app and the deck |
| R4 | Missed S-dominant rhythms | Medium | Medium | Rate and pause rules; S findings capped at Amber |
| R5 | Copilot hallucinates clinical advice | Medium | High | Citation enforcement, safety classifier, template fallback, golden-set audit |
| R6 | Privacy or consent gap | Medium | High | Consent gate, RBAC, encryption, audit |
| R7 | Hardware delay | Medium | Medium | App built on ReplaySource first; spare kits |

**Clinical safety position:** CardioSense AI is a decision-support and monitoring tool. It does not diagnose, does not provide emergency response, and must not be the only basis for treatment. Every screen carries a disclaimer; every AI insight is labelled as generated and needs clinician review.

---

## 15. Demo narrative for judges

| Time | Action | What the judges see |
|---|---|---|
| 0:00 | The problem: palpitations and a normal Holter | The gap, before any technology |
| 0:40 | Fit the sensor to a team member; show the kit | Live ECG with N labels: this is real |
| 1:10 | Log in as a clinician; open the patient | Role-based access |
| 1:40 | Point at the beat labels, ribbon and rhythm status | Every beat is labelled with a confidence |
| 2:20 | Switch to replay of MIT-BIH record 119 or 208 | V beats appear; ribbon turns coral; risk gauge rises; Orange alert fires. Ground-truth labels match |
| 3:00 | Open the alert; show the evidence snapshot; acknowledge | The alert is a workflow |
| 3:30 | Ask the copilot "What changed since yesterday?" | A cited answer linking to real events |
| 4:10 | Show the honest-evaluation slide | 0.92 V sensitivity on unseen patients; 99.3% on the beat-wise split |
| 4:40 | Trends and PDF export; close on cost and validation plan | Longitudinal value and a credible next step |

**Resilience checklist:** recorded fallback video; replay mode with a "DEMO" badge if the hardware fails; seed data for three patients (stable, watch, critical); two laptops and spare electrodes; a one-page handout with the architecture and metrics.

**Good replay records:** 100 (mostly normal), 119 and 208 (frequent V beats), 220 and 223 (S beats). Avoid 232 (known model weakness).

---

## 16. Regulatory and ethical considerations

| Question | R1 position | Path to regulated release |
|---|---|---|
| Medical device? | No; research and demonstration only | Likely Class IIa (EU MDR); 510(k) in the US |
| Regulator (Bangladesh) | Not submitted | DGDA engagement after pilot evidence |
| Clinical claims | Screening support; no diagnosis | Validated indications only, after a prospective trial |
| Quality system | Design controls started | ISO 13485 QMS before submission |
| Clinical evidence | Retrospective patient-wise and external tests | Prospective, multi-site, pre-registered protocol |
| Ethics | Informed consent; no vulnerable participants in the demo | Ethics committee approval for the pilot |

**Principles:** augment, don't replace; uncertainty is visible; validate across age, sex and body type; patients can see who accessed their data and withdraw consent; data minimisation.

---

## 17. Appendix A — API and data model

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/v1/auth/register` · `/login` · `/refresh` · `/logout` | Account lifecycle |
| POST | `/v1/auth/mfa/enrol` · `/verify` | TOTP |
| GET | `/v1/patients?cursor=&risk=` | Patient list with risk and signal status |
| POST | `/v1/patients` | Create patient and consent record |
| POST | `/v1/devices/pair` | Pair a device |
| WS | `/v1/stream/{device_id}` | Live frames in; beats, rhythm windows and alerts out |
| POST | `/v1/demo/replay` | Start a replay `{record, dataset, speed}` |
| GET | `/v1/sessions/{id}/beats?from=&to=` | Beat labels for a time range |
| GET | `/v1/sessions/{id}/events` | Rhythm windows, alerts, annotations |
| GET | `/v1/patients/{id}/trends?range=30d` | HR, HRV, S/V burden |
| POST | `/v1/copilot/query` | Cited insight or follow-up |
| POST | `/v1/alerts/{id}/ack` · `/escalate` | Alert workflow |
| GET | `/v1/reports/{id}.pdf` | Report download |
| GET | `/v1/audit?actor=&from=` | Audit search (admin) |

**Frame (device or replay → server):** `{seq, device_id, t0, fs, samples[], lead_off, source: "serial" | "replay"}`

**Beat (server → client):** `{sample, time_s, label: "N" | "S" | "V" | "F" | "Unknown", confidence, probs[4], model_version}`

| Entity | Key fields | Relationships |
|---|---|---|
| Organisation | id, name, plan, retention policy | Has users, patients |
| User | id, email, role, MFA state | Belongs to organisation |
| Patient | id, demographics, consent, risk band | Has devices, sessions, alerts |
| Device | id, firmware, sample rate, last seen, SQI | Belongs to patient; emits frames |
| Session | id, start, end, device, source, model and rule versions | Has beats, rhythm windows, alerts |
| **Beat** | session, sample index, time, label, confidence, probabilities | Belongs to session |
| RhythmWindow | start, end, HR, findings[], S/V burden, SQI, rule version | Belongs to session; feeds risk |
| Alert | rule ID, tier, evidence snapshot, state, acknowledgement | Belongs to patient; assigned to clinician |
| Insight | text, citations[], model, generated_at | Belongs to patient or session |

---

## 18. Appendix B — Assumptions and data provenance

| Claim | Status | Source |
|---|---|---|
| V sensitivity 0.92, macro-F1 0.547 on DS2 (ensemble) | Measured | `cardiosense_beat_model/metrics_test.json` |
| INCART macro-F1 0.619, S sensitivity 0.91 (ensemble) | Measured | `metrics_test.json` |
| DS2 mean over 3 seeds: macro-F1 0.535 ± 0.007 | Measured | `model_config.json` → evaluation |
| INCART mean over 3 seeds: macro-F1 0.601 ± 0.012 | Measured | `model_config.json` → evaluation |
| 5-fold CV on DS1: macro-F1 0.560 | Measured | `cv3_summary.csv` |
| Beat-wise split: accuracy 0.993, macro-F1 0.936 | Measured | `metrics_test.json` |
| Beat detection 0.980 / 0.999; 60–250 Hz and noise stress results | Measured | `stress_tests.csv` |
| Unknown threshold table | Measured | `model_config.json` → evaluation |
| ~180× real time on one CPU core | Measured | Backend check, Kaggle notebook step 9 |
| Rules engine thresholds | Assumption | Common clinical conventions; to be reviewed by the clinical advisor |
| Performance on AD8232 recordings | Not yet measured | R1 target |
| Latency < 3 s, 50 streams, 99.5% uptime | Target | Engineering plan |
| BOM ~$22, pricing, market sizes | Illustrative | Supplier list prices, comparable SaaS |
| CVD mortality 17.9M / year | External | WHO fact sheet |

**Splits:** DS1 / DS2 follow de Chazal et al. (2004). DS1 = 101, 106, 108, 109, 112, 114, 115, 116, 118, 119, 122, 124, 201, 203, 205, 207, 208, 209, 215, 220, 223, 230. DS2 = 100, 103, 105, 111, 113, 117, 121, 123, 200, 202, 210, 212, 213, 214, 219, 221, 222, 228, 231, 232, 233, 234. Paced records 102, 104, 107 and 217 are excluded.

---

## 19. Appendix C — Glossary and references

| Term | Meaning |
|---|---|
| N / S / V / F | AAMI beat classes: normal, supraventricular ectopic, ventricular ectopic, fusion |
| PVC / VEB | Premature ventricular contraction / ventricular ectopic beat (class V) |
| Patient-wise split | No patient appears in both training and test data |
| Beat-wise split | Beats are split randomly, so the same patient appears on both sides; inflates results |
| Sensitivity (Se) | Share of true beats of a class that the model finds |
| Positive predictivity (+P) | Share of the model's labels for a class that are correct |
| Macro-F1 | Average F1 over the four classes, so rare classes count equally |
| Ectopic burden | Percentage of beats that are S or V |
| RR interval | Time between consecutive heartbeats |
| HRV | Heart-rate variability (SDNN, RMSSD) |
| SQI | Signal quality index, 0–1 |
| SWA | Stochastic weight averaging: averaging model weights over the last epochs |
| AFib | Atrial fibrillation; not detected by a dedicated model in R1 |
| RAG | Retrieval-augmented generation |

**References**

1. World Health Organization. Cardiovascular diseases (CVDs) fact sheet.
2. Moody GB, Mark RG. The impact of the MIT-BIH Arrhythmia Database. IEEE EMB Magazine, 2001.
3. Goldberger AL, et al. PhysioBank, PhysioToolkit, and PhysioNet. Circulation, 2000.
4. de Chazal P, O'Dwyer M, Reilly RB. Automatic classification of heartbeats using ECG morphology and heartbeat interval features. IEEE TBME, 2004.
5. ANSI/AAMI EC57: Testing and reporting performance results of cardiac rhythm and ST segment measurement algorithms.
6. Tihonenko V, et al. St Petersburg INCART 12-lead Arrhythmia Database. PhysioNet.
7. Moody GB, Muldrow WE, Mark RG. A noise stress test for arrhythmia detectors. Computers in Cardiology, 1984.
8. Pan J, Tompkins WJ. A real-time QRS detection algorithm. IEEE TBME, 1985.
9. He K, et al. Deep residual learning for image recognition. CVPR, 2016.
10. Izmailov P, et al. Averaging weights leads to wider optima and better generalization (SWA). UAI, 2018.
11. OWASP ASVS 4.0; WCAG 2.1; ISO 13485; ISO 27001.

---

*End of document. CardioSense AI · CSAI-BRD-001 · v1.1 · November 2026. A planning artefact for a hackathon and early partner discussions; not a clinical protocol, regulatory submission or medical advice.*
