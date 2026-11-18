/** DEV MOCK ONLY — in-memory "database" for the mock REST API. */
import type {
  Alert, AuditEntry, Beat, Device, Insight, Patient, PatientNote, RulesConfig, Session, Tier, User,
} from '../api/types';
import { DISCLAIMER } from '../api/types';
import { hash, mockWindow, profileFor, renderRecording, resample } from './synth';

export const MODEL_VERSION = 'mock-beat-model-0.0.0';
export const RULE_VERSION = 'rules-mock-1.0.0';
export const PASSWORD = 'CardioSense!23';
export const FS = 360;

const iso = (msAgo: number) => new Date(Date.now() - msAgo).toISOString();
const MIN = 60_000, HOUR = 3_600_000, DAY = 86_400_000;
let idSeq = 1000;
export const newId = (p: string) => `${p}-${(++idSeq).toString(36)}`;

export const users: (User & { password: string })[] = [
  { id: 'u-admin', email: 'admin@cardiosense.dev', name: 'Demo Admin', role: 'org_admin', org_id: 'org-demo', mfa_enabled: false, active: true, password: PASSWORD },
  { id: 'u-clin', email: 'clinician@cardiosense.dev', name: 'Demo Clinician', role: 'clinician', org_id: 'org-demo', mfa_enabled: false, active: true, password: PASSWORD },
  { id: 'u-tech', email: 'tech@cardiosense.dev', name: 'Demo Technician', role: 'technician', org_id: 'org-demo', mfa_enabled: false, active: true, password: PASSWORD },
  { id: 'u-pat', email: 'patient@cardiosense.dev', name: 'Demo Patient', role: 'patient', org_id: 'org-demo', mfa_enabled: false, active: true, password: PASSWORD },
  // mock-only account that exercises the OTP step (any 6 digits are accepted)
  { id: 'u-mfa', email: 'mfa@cardiosense.dev', name: 'Demo MFA Clinician', role: 'clinician', org_id: 'org-demo', mfa_enabled: true, active: true, password: PASSWORD },
];

const patient = (id: string, name: string, extra: Partial<Patient>): Patient => ({
  id, name, mrn: null, dob: null, sex: null, notes: null, consent: true, is_demo: false, risk_score: null, risk_tier: null,
  last_event: null, signal_status: 'offline', active_session_id: null, device_id: null, open_alerts: 0, ...extra,
});
export const patients: Patient[] = [
  patient('pt-demo-208', 'Demo patient — critical', { mrn: 'DEMO-208', dob: '1958-03-14', sex: 'M', is_demo: true, device_id: 'dev-demo-208', notes: 'Replay of MIT-BIH record 208.' }),
  patient('pt-demo-223', 'Demo patient — watch', { mrn: 'DEMO-223', dob: '1966-11-02', sex: 'M', is_demo: true, device_id: 'dev-demo-223', notes: 'Replay of MIT-BIH record 223.' }),
  patient('pt-demo-100', 'Demo patient — stable', { mrn: 'DEMO-100', dob: '1971-07-21', sex: 'M', is_demo: true, device_id: 'dev-demo-100', notes: 'Replay of MIT-BIH record 100.' }),
  patient('pt-1001', 'Example Patient A', { mrn: 'MOCK-1001', dob: '1972-01-30', sex: 'M', device_id: 'dev-ad8232-01', risk_score: 8, risk_tier: 'green', last_event: null }),
  patient('pt-1002', 'Example Patient B', { mrn: 'MOCK-1002', dob: '1985-09-09', sex: 'F', consent: false, notes: 'Consent not recorded yet — streaming is blocked.' }),
];
export const demoRecordOf: Record<string, string> = { 'pt-demo-208': '208', 'pt-demo-223': '223', 'pt-demo-100': '100' };

export const devices: Device[] = [
  { device_id: 'dev-demo-208', name: 'Replay device 208', patient_id: 'pt-demo-208', firmware: 'sender-1.0', paired: true, last_seen: iso(2 * MIN) },
  { device_id: 'dev-demo-223', name: 'Replay device 223', patient_id: 'pt-demo-223', firmware: 'sender-1.0', paired: true, last_seen: iso(3 * HOUR) },
  { device_id: 'dev-demo-100', name: 'Replay device 100', patient_id: 'pt-demo-100', firmware: 'sender-1.0', paired: true, last_seen: iso(2 * MIN) },
  { device_id: 'dev-ad8232-01', name: 'AD8232 kit 01', patient_id: 'pt-1001', firmware: 'esp32-0.3.1', paired: true, last_seen: iso(26 * HOUR), signal_quality: 0.91 },
];
export const pairing = new Map<string, { device_id: string; expires: number }>();

// ───────────── stored sessions (signal is regenerated deterministically from the seed) ─────────────
export interface StoredSession extends Session { seed: number; record: string }
export const sessions: StoredSession[] = [];
const recCache = new Map<string, { mv: number[]; beats: Beat[] }>();
export function recordingOf(s: StoredSession) {
  const key = `${s.id}:${s.duration_s ?? 0}`;
  let r = recCache.get(key);
  if (!r) {
    r = renderRecording(s.record, s.fs, Math.min(900, s.duration_s ?? 120), s.seed, MODEL_VERSION);
    recCache.set(key, r);
    if (recCache.size > 6) recCache.delete(recCache.keys().next().value as string);
  }
  return r;
}
export function windowsOf(s: StoredSession) {
  const { beats } = recordingOf(s);
  const out = [];
  for (let e = 10; e <= (s.duration_s ?? 0); e += 10) {
    const inWin = beats.filter((b) => b.time_s >= e - 10 && b.time_s < e);
    const lastMin = beats.filter((b) => b.time_s >= e - 60 && b.time_s < e);
    out.push(mockWindow(`${s.id}-w${e / 10}`, e - 10, e, inWin, lastMin, RULE_VERSION));
  }
  return out;
}
export function addSession(patientId: string, record: string, startedMsAgo: number, durationS: number, extra: Partial<StoredSession> = {}): StoredSession {
  const id = extra.id ?? newId('ses');
  const s: StoredSession = {
    id, patient_id: patientId, device_id: `dev-demo-${record}`, fs: FS, source: 'replay', record, dataset: 'MIT-BIH',
    started_at: iso(startedMsAgo), ended_at: iso(startedMsAgo - durationS * 1000), active: false, model_version: MODEL_VERSION,
    rule_version: RULE_VERSION, model_loaded: true, duration_s: durationS, seed: hash(id), ...extra,
  };
  sessions.unshift(s);
  return s;
}
export function summarise(s: StoredSession): Session {
  const { seed: _seed, ...pub } = s;
  void _seed;
  if (s.active) return pub;
  const { beats } = recordingOf(s);
  const counts = { N: 0, S: 0, V: 0, F: 0, Unknown: 0 };
  beats.forEach((b) => { counts[b.label]++; });
  const known = beats.length - counts.Unknown || 1;
  const wins = windowsOf(s);
  const hrs = wins.map((w) => w.hr_bpm).filter((x): x is number => x !== null);
  return {
    ...pub,
    summary: {
      duration_s: s.duration_s, beats: beats.length, beat_counts: counts,
      mean_hr_bpm: hrs.length ? Math.round((hrs.reduce((a, b) => a + b, 0) / hrs.length) * 10) / 10 : null,
      s_burden_pct: Math.round((1000 * counts.S) / known) / 10, v_burden_pct: Math.round((1000 * counts.V) / known) / 10,
      max_risk_score: Math.max(0, ...wins.map((w) => w.risk.score)), alerts: alerts.filter((a) => a.session_id === s.id).length, gaps: 0,
    },
  };
}

// ───────────── alerts ─────────────
export const alerts: Alert[] = [];
export const alertHistory = new Map<string, { at: string; action: string; actor: string; note: string | null }[]>();
export function evidenceFor(s: StoredSession, startS: number, endS: number): Alert['evidence'] {
  const { mv, beats } = recordingOf(s);
  const strip = resample(mv.slice(Math.round(startS * s.fs), Math.round(endS * s.fs)), s.fs, 250);
  const inWin = beats.filter((b) => b.time_s >= startS && b.time_s < endS);
  const win = windowsOf(s).find((w) => w.end_s >= endS) ?? null;
  return { start_s: startS, end_s: endS, fs: 250, strip_mv: strip, beats: inWin, window: win, rule_version: RULE_VERSION, model_version: MODEL_VERSION };
}
export function addAlert(a: Omit<Alert, 'id' | 'updated_at' | 'acknowledged_by' | 'note' | 'state'> & Partial<Alert>): Alert {
  const full: Alert = { id: newId('alr'), state: 'open', updated_at: a.created_at, acknowledged_by: null, note: null, ...a };
  alerts.unshift(full);
  alertHistory.set(full.id, [{ at: full.created_at, action: 'created', actor: 'rules engine', note: null }]);
  return full;
}
export const withExtras = (a: Alert): Alert => ({
  ...a,
  patient_name: patients.find((p) => p.id === a.patient_id)?.name,
  history: alertHistory.get(a.id) ?? [],
});

// ───────────── audit, notes, insights, rules ─────────────
export const audit: AuditEntry[] = [];
export function log(actor: string, action: string, target: string, detail?: unknown) {
  audit.unshift({ id: newId('aud'), at: new Date().toISOString(), actor_email: actor, action, target, detail: detail ?? null });
}
export const notes = new Map<string, PatientNote[]>();
export const insights = new Map<string, Insight[]>();
export let rules: RulesConfig = {
  version: RULE_VERSION,
  updated_at: iso(9 * DAY),
  updated_by: 'admin@cardiosense.dev',
  thresholds: {
    brady_bpm: 50, brady_severe_bpm: 40, tachy_bpm: 100, tachy_severe_bpm: 150, pause_s: 2.0, pause_critical_s: 3.0,
    v_run_min_beats: 3, v_sustained_s: 30, bigeminy_min_cycles: 6, s_run_min_beats: 3, burden_amber_pct: 1, burden_orange_pct: 10,
    irregular_rmssd_ratio: 0.12, low_sqi: 0.5, alert_cooldown_s: 300,
  },
};
export function setRules(thresholds: Record<string, unknown>, by: string) {
  const n = Number(/(\d+)$/.exec(rules.version)?.[1] ?? 0) + 1;
  rules = { version: rules.version.replace(/\d+$/, String(n)), thresholds, updated_at: new Date().toISOString(), updated_by: by };
  return rules;
}

export function makeInsight(patientId: string, question?: string): Insight {
  const p = patients.find((x) => x.id === patientId);
  const mine = alerts.filter((a) => a.patient_id === patientId).slice(0, 3);
  const ses = sessions.filter((s) => s.patient_id === patientId && !s.active);
  const cites = [
    ...mine.map((a) => ({ id: a.id, kind: 'alert', label: a.title, time: a.created_at })),
    ...ses.slice(0, 1).map((s) => ({ id: s.id, kind: 'session', label: `Session ${s.record ? `record ${s.record}` : ''}`.trim(), time: s.started_at })),
  ];
  let text: string;
  if (!cites.length) {
    text = 'There is not enough recorded evidence for this patient to summarise. No sessions or alerts were retrieved.';
  } else {
    const parts: string[] = [];
    if (question) parts.push(`Regarding "${question.slice(0, 80)}": the answer is limited to the retrieved records.`);
    mine.forEach((a) => parts.push(`${a.title} was recorded (${a.detail.replace(/\.$/, '')}) [${a.id}].`));
    if (ses[0]) {
      const sm = summarise(ses[0]).summary;
      parts.push(`The most recent stored session shows a V burden of ${sm?.v_burden_pct ?? '—'}% and a mean heart rate of ${sm?.mean_hr_bpm ?? '—'} bpm [${ses[0].id}].`);
    }
    parts.push('Consider reviewing the evidence strips before acting; confidence is limited by single-lead data.');
    text = parts.join(' ');
  }
  const ins: Insight = {
    id: newId('ins'), text, citations: cites, generated_by: 'template', model: null, generated_at: new Date().toISOString(),
    disclaimer: DISCLAIMER, question: question ?? null, patient_id: patientId,
  };
  if (p) insights.set(patientId, [ins, ...(insights.get(patientId) ?? [])]);
  return ins;
}

// ───────────── seed ─────────────
function seed() {
  const s208 = addSession('pt-demo-208', '208', 26 * HOUR, 180, { id: 'ses-208-a' });
  addSession('pt-demo-208', '208', 3 * DAY, 240, { id: 'ses-208-b' });
  const s223 = addSession('pt-demo-223', '223', 5 * HOUR, 180, { id: 'ses-223-a' });
  addSession('pt-demo-100', '100', 2 * DAY, 150, { id: 'ses-100-a' });

  const firstWith = (s: StoredSession, rule: string) => windowsOf(s).find((w) => w.findings.some((f) => f.rule_id === rule));
  const mk = (s: StoredSession, rule: string, tier: Tier, title: string, agoMs: number, fallbackDetail: string) => {
    const w = firstWith(s, rule) ?? windowsOf(s)[2];
    const f = w.findings.find((x) => x.rule_id === rule);
    return addAlert({
      patient_id: s.patient_id, session_id: s.id, rule_id: rule, tier, title, detail: `${f?.detail ?? fallbackDetail}.`,
      created_at: iso(agoMs), evidence: evidenceFor(s, w.start_s, w.end_s),
    });
  };
  const a1 = mk(s208, 'V_BURDEN', 'orange', 'V burden above 10%', 26 * HOUR - 40_000, 'V burden above 10% over the last minute');
  const a2 = mk(s208, 'V_COUPLET', 'amber', 'V couplet', 26 * HOUR - 20_000, '2 consecutive V beats');
  mk(s223, 'S_BURDEN', 'amber', 'S burden above 1%', 5 * HOUR - 60_000, 'S burden above 1% over the last minute');
  a2.state = 'acknowledged';
  a2.acknowledged_by = 'clinician@cardiosense.dev';
  a2.note = 'Seen on review. Monitoring.';
  a2.updated_at = iso(25 * HOUR);
  alertHistory.get(a2.id)?.push({ at: a2.updated_at, action: 'acknowledged', actor: 'clinician@cardiosense.dev', note: a2.note });
  void a1;

  notes.set('pt-demo-208', [{ id: 'n1', text: 'Reports palpitations after climbing stairs.', kind: 'symptom', author: 'Demo Clinician', created_at: iso(27 * HOUR) }]);
  makeInsight('pt-demo-208');
  log('clinician@cardiosense.dev', 'alert.acknowledge', `alert ${a2.id}`, { note: a2.note });
  log('admin@cardiosense.dev', 'rules.update', 'rules', { version: RULE_VERSION });
  log('tech@cardiosense.dev', 'device.pair', 'device dev-ad8232-01');
  log('clinician@cardiosense.dev', 'auth.login', 'user u-clin');
  refreshPatients();
}

const TIER_RANK: Record<Tier, number> = { green: 0, amber: 1, orange: 2, red: 3 };
/** Recompute derived patient fields (open alerts, last event, risk from the latest stored session if offline). */
export function refreshPatients() {
  for (const p of patients) {
    const mine = alerts.filter((a) => a.patient_id === p.id);
    p.open_alerts = mine.filter((a) => a.state === 'open' || a.state === 'escalated').length;
    const lastA = mine[0];
    if (lastA) p.last_event = { label: lastA.title, tier: lastA.tier, time: lastA.created_at };
    if (p.signal_status === 'offline') {
      const s = sessions.find((x) => x.patient_id === p.id && !x.active);
      if (s) {
        const w = windowsOf(s);
        const lastW = w[w.length - 1];
        if (lastW) { p.risk_score = lastW.risk.score; p.risk_tier = lastW.risk.tier; }
      }
    }
  }
}
export const sortedPatients = () =>
  [...patients].sort((a, b) => (TIER_RANK[b.risk_tier ?? 'green'] - TIER_RANK[a.risk_tier ?? 'green']) || ((b.risk_score ?? -1) - (a.risk_score ?? -1)));

seed();
export { profileFor };
