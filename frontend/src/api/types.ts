/**
 * Wire types. Everything under "CONTRACT" mirrors docs/CONTRACT.md exactly (§2.2 live WebSocket, §4 REST).
 * Shapes the contract names but does not spell out are under "ASSUMED" — they are deliberately loose
 * (optional fields, aliases) and are read through tolerant normalisers in `normalize.ts`.
 */

// ───────────────────────────── CONTRACT §2.2 — shared primitives ─────────────────────────────

export type Tier = 'green' | 'amber' | 'orange' | 'red';
export const TIERS: readonly Tier[] = ['green', 'amber', 'orange', 'red'];

export type BeatLabel = 'N' | 'S' | 'V' | 'F' | 'Unknown';
export const BEAT_LABELS: readonly BeatLabel[] = ['N', 'S', 'V', 'F', 'Unknown'];

export type RuleId =
  | 'BRADY' | 'BRADY_SEVERE' | 'TACHY' | 'TACHY_SEVERE' | 'PAUSE' | 'PAUSE_CRITICAL'
  | 'V_COUPLET' | 'V_RUN' | 'V_SUSTAINED' | 'BIGEMINY' | 'TRIGEMINY' | 'S_RUN' | 'S_BURDEN'
  | 'V_BURDEN' | 'IRREGULAR' | 'LEAD_OFF' | 'LOW_SQI'
  | (string & {});

export type StreamSource = 'serial' | 'replay' | (string & {});

export interface BeatProbs { N: number; S: number; V: number; F: number }

export interface Beat {
  sample: number;
  time_s: number;
  label: BeatLabel;
  confidence: number;
  probs: BeatProbs;
  model_version: string;
  /** AAMI class of the matched replay annotation, or null. */
  truth: BeatLabel | null;
}

export interface Finding {
  rule_id: RuleId;
  label: string;
  severity: Tier;
  start_s: number;
  end_s: number;
  value: number | null;
  detail: string;
}

export interface RiskFactor { text: string; points: number; rule_id: RuleId }
export interface Risk { score: number; tier: Tier; factors: RiskFactor[] }

export type BeatCounts = Record<BeatLabel, number>;

export interface RhythmWindow {
  id?: string;
  start_s: number;
  end_s: number;
  hr_bpm: number | null;
  findings: Finding[];
  s_burden_pct: number;
  v_burden_pct: number;
  sqi: number;
  sdnn_ms: number | null;
  rmssd_ms: number | null;
  beat_counts: BeatCounts;
  rule_version: string;
  risk: Risk;
}

export type AlertState = 'open' | 'acknowledged' | 'escalated' | 'closed';
export const ALERT_STATES: readonly AlertState[] = ['open', 'acknowledged', 'escalated', 'closed'];

export interface AlertEvidence {
  start_s: number;
  end_s: number;
  fs: number;
  strip_mv: number[];
  beats: Beat[];
  window?: Partial<RhythmWindow> | null;
  rule_version: string;
  model_version: string;
  // extras the backend adds
  /** Why the alert was confirmed (e.g. seen in 2 consecutive windows). */
  reason?: string | null;
  finding?: Finding | null;
}

export interface Alert {
  id: string;
  patient_id: string;
  session_id: string;
  rule_id: RuleId;
  tier: Tier;
  title: string;
  detail: string;
  state: AlertState;
  created_at: string;
  updated_at: string;
  acknowledged_by: string | null;
  note: string | null;
  evidence: AlertEvidence;
  // ASSUMED (optional extras the UI uses when present)
  patient_name?: string;
  history?: AlertTransition[];
  transitions?: AlertTransition[];
}

/** ASSUMED: audit trail entries for an alert, if the API embeds them. */
export interface AlertTransition {
  at?: string; created_at?: string;
  action?: string; state?: AlertState; from_state?: string; to_state?: string;
  actor?: string; actor_email?: string; by?: string;
  note?: string | null;
}

// ───────────────────────────── CONTRACT §2.2 — live messages ─────────────────────────────

export interface SessionMsg {
  type: 'session';
  session_id: string;
  patient_id: string;
  device_id: string;
  fs: number;
  source: StreamSource;
  record?: string | null;
  dataset?: string | null;
  started_at: string;
  model_version: string;
  rule_version: string;
  model_loaded: boolean;
  active: boolean;
}
export interface SignalMsg { type: 'signal'; t0: number; fs: number; mv: number[]; lead_off: boolean }
export interface BeatMsg extends Beat { type: 'beat' }
export interface WindowMsg extends RhythmWindow { type: 'window'; id: string }
export interface AlertMsg { type: 'alert'; alert: Alert }
export type QualityState = 'ok' | 'lead_off' | 'low_sqi' | 'gap';
export interface QualityMsg { type: 'quality'; state: QualityState; detail?: string; sqi?: number }
export interface StatusMsg { type: 'status'; stream: 'connected' | 'disconnected'; last_sample_time?: string | null }

export type LiveMsg = SessionMsg | SignalMsg | BeatMsg | WindowMsg | AlertMsg | QualityMsg | StatusMsg;
export type LiveMsgType = LiveMsg['type'];

// ───────────────────────────── CONTRACT §4 — REST ─────────────────────────────

export type Role = 'patient' | 'technician' | 'clinician' | 'org_admin' | 'system_admin';
export const ROLES: readonly Role[] = ['patient', 'technician', 'clinician', 'org_admin', 'system_admin'];

export interface User {
  id: string;
  email: string;
  name: string;
  role: Role;
  org_id: string | null;
  mfa_enabled: boolean;
  // extras the backend adds
  active?: boolean;
  patient_id?: string | null;
}

export interface LoginRequest { email: string; password: string; otp?: string }
export interface LoginResponse { access_token: string; refresh_token: string; token_type: 'bearer'; user: User }
/** ASSUMED: refresh takes `{refresh_token}` and returns a new pair (rotating); `user` optional. */
export interface RefreshResponse { access_token: string; refresh_token: string; token_type?: 'bearer'; user?: User }

export type SignalStatus = 'streaming' | 'offline' | 'lead_off' | (string & {});

/** ASSUMED shape of `Patient.last_event`: a plain string or a small object. */
export type LastEvent =
  | string
  | { label?: string; title?: string; rule_id?: string; tier?: Tier; severity?: Tier; time?: string; at?: string; created_at?: string }
  | null;

export interface Patient {
  id: string;
  name: string;
  mrn: string | null;
  dob: string | null;
  sex: string | null;
  notes: string | null;
  consent: boolean;
  is_demo: boolean;
  risk_score: number | null;
  risk_tier: Tier | null;
  last_event: LastEvent;
  signal_status: SignalStatus;
  active_session_id: string | null;
  device_id: string | null;
  open_alerts: number;
}

export interface PatientCreate { name: string; mrn?: string; dob?: string; sex?: string; notes?: string; consent: boolean }

/** ASSUMED */
export interface PatientNote {
  id: string;
  text: string;
  kind?: 'note' | 'symptom' | (string & {});
  author?: string | null; author_name?: string | null;
  created_at: string;
}

/** ASSUMED */
export interface Device {
  id?: string;
  device_id: string;
  name?: string | null;
  patient_id?: string | null;
  patient_name?: string | null;
  firmware?: string | null;
  paired?: boolean;
  last_seen?: string | null;
  signal_quality?: number | null; sqi?: number | null;
  status?: string | null;
  // returned by POST /devices
  pairing_code?: string | null;
  pairing_expires_at?: string | null; expires_at?: string | null;
}
export interface PairResponse { device_id: string; device_key: string }

/** ASSUMED: session metadata + summary stats. */
export interface SessionSummary {
  duration_s?: number | null;
  beats?: number | null;
  beat_counts?: Partial<BeatCounts> | null;
  hr_bpm?: number | null; mean_hr_bpm?: number | null;
  s_burden_pct?: number | null;
  v_burden_pct?: number | null;
  max_risk_score?: number | null; risk_score?: number | null; max_risk_tier?: Tier | null;
  alerts?: number | null;
  gaps?: number | null;
  [k: string]: unknown;
}
export interface Session {
  id: string;
  patient_id: string;
  device_id?: string | null;
  fs: number;
  source?: StreamSource | null;
  record?: string | null;
  dataset?: string | null;
  started_at: string;
  ended_at?: string | null;
  active?: boolean;
  model_version?: string | null;
  rule_version?: string | null;
  model_loaded?: boolean;
  duration_s?: number | null;
  summary?: SessionSummary | null;
}

/** CONTRACT: `/sessions/{id}/signal` → `{fs,t0,mv[]}`. */
export interface SignalChunk { fs: number; t0: number; mv: (number | null)[] }

/** ASSUMED: `/sessions/{id}/events` items — rhythm windows with findings, alerts, gaps, quality changes. */
export interface SessionEvent {
  id?: string;
  kind?: 'window' | 'finding' | 'alert' | 'gap' | 'quality' | (string & {}); type?: string;
  rule_id?: RuleId | null;
  label?: string | null; title?: string | null;
  severity?: Tier | null; tier?: Tier | null;
  start_s?: number | null; end_s?: number | null; time_s?: number | null;
  detail?: string | null;
  at?: string | null; created_at?: string | null;
  alert_id?: string | null;
  findings?: Finding[];
  risk?: Risk;
  hr_bpm?: number | null;
}

/** CONTRACT: trend bucket. */
export interface TrendBucket {
  t: string;
  hr_bpm: number | null;
  sdnn_ms: number | null;
  rmssd_ms: number | null;
  s_burden_pct: number | null;
  v_burden_pct: number | null;
  risk_score: number | null;
  findings: (Finding | string | { rule_id?: string; label?: string; count?: number; severity?: Tier })[] | Record<string, number> | null;
}
/** ASSUMED gap shape. */
export interface TrendGap { start: string; end: string; from?: string; to?: string }
export type TrendRange = '24h' | '7d' | '30d' | '90d';
export const TREND_RANGES: readonly TrendRange[] = ['24h', '7d', '30d', '90d'];
export interface Trends { range?: TrendRange; bucket_s?: number; from?: string; to?: string; buckets: TrendBucket[]; gaps: TrendGap[] }

/** CONTRACT: copilot. */
export interface Citation { id: string; kind: string; label: string; time: string | null }
export interface Insight {
  id: string;
  text: string;
  citations: Citation[];
  generated_by: 'llm' | 'template';
  model: string | null;
  generated_at: string;
  disclaimer: string;
  // ASSUMED
  question?: string | null;
  patient_id?: string;
}
export interface CopilotRequest { patient_id: string; question?: string }

/** ASSUMED */
export interface DemoRecord {
  record: string;
  dataset: string;
  description?: string | null; label?: string | null;
  duration_s?: number | null;
  fs?: number | null;
}
export interface DemoReplayRequest { record: string; dataset: string; speed: number; patient_id?: string }
/** ASSUMED */
export interface DemoReplayResponse { patient_id?: string; device_id?: string; record?: string; status?: string; detail?: string }

/** ASSUMED */
export interface AuditEntry {
  id: string;
  at?: string; created_at?: string; time?: string;
  actor?: string | null; actor_email?: string | null; actor_id?: string | null;
  action: string;
  target?: string | null; target_type?: string | null; target_id?: string | null; entity?: string | null; entity_id?: string | null;
  detail?: unknown;
  ip?: string | null;
}

/** ASSUMED: versioned thresholds; unknown keys are rendered generically. */
export interface RulesConfig {
  version: string;
  thresholds: Record<string, unknown>;
  updated_at?: string | null;
  updated_by?: string | null;
}

/** CONTRACT: `/meta`. `model` is model_config.json passthrough; all optional, rendered generically. */
export interface ModelInfo {
  model_version?: string;
  stand_in?: boolean;
  stand_in_note?: string;
  architecture?: string;
  classes?: string[];
  evaluation_note?: string;
  evaluation?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
  limitations?: string[];
  unknown_threshold?: number;
  [k: string]: unknown;
}
export interface Meta {
  model_version: string | null;
  rule_version: string;
  model_loaded: boolean;
  disclaimer: string;
  model?: ModelInfo | null;
  // tolerated flat variants
  metrics?: Record<string, unknown> | null;
  evaluation?: Record<string, unknown> | null;
  limitations?: string[] | null;
  stand_in?: boolean;
  stand_in_note?: string;
  [k: string]: unknown;
}

export interface Health { status: string; [k: string]: unknown }

export const DISCLAIMER = 'Research and demonstration system — not a medical device. Not an emergency service.';
