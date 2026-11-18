/** DEV MOCK ONLY — in-browser implementation of the REST endpoints the UI uses (CONTRACT §4). */
import type { Alert, AlertState, Role, Tier, TrendBucket, User } from '../api/types';
import { DISCLAIMER } from '../api/types';
import * as db from './db';
import { PROFILES, rng, hash } from './synth';
import { activeStream, demoPatientForRecord, startStream, stopStream } from './stream';

type Handler = (ctx: { m: RegExpExecArray; q: URLSearchParams; body: Record<string, unknown>; user: User }) => unknown;
class HttpError extends Error { constructor(public status: number, detail: string) { super(detail); } }
const fail = (status: number, detail: string): never => { throw new HttpError(status, detail); };

// ── fake JWTs (unsigned; only the `exp` claim matters to the client) ──
const b64 = (o: object) => btoa(JSON.stringify(o)).replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_');
const refreshTokens = new Map<string, string>(); // token -> user id
function issue(u: User) {
  const exp = Math.floor(Date.now() / 1000) + 15 * 60;
  const access = `${b64({ alg: 'none', typ: 'JWT' })}.${b64({ sub: u.id, role: u.role, exp })}.mock`;
  const refresh = `mock-refresh-${Math.random().toString(36).slice(2)}`;
  refreshTokens.set(refresh, u.id);
  return { access_token: access, refresh_token: refresh, token_type: 'bearer' as const, user: pub(u) };
}
const pub = (u: User & { password?: string }): User => { const { password: _p, ...rest } = u; void _p; return rest; };
function userFromAuth(h: string | null): User | null {
  const tok = h?.replace(/^Bearer\s+/i, '') ?? '';
  try {
    const payload = JSON.parse(atob((tok.split('.')[1] ?? '').replace(/-/g, '+').replace(/_/g, '/'))) as { sub: string; exp: number };
    if (payload.exp * 1000 < Date.now()) return null;
    return db.users.find((u) => u.id === payload.sub) ?? null;
  } catch { return null; }
}
const need = (user: User, ...roles: Role[]) => { if (!roles.includes(user.role)) fail(403, 'Not permitted for your role.'); };
const visiblePatients = (u: User) => (u.role === 'patient' ? db.sortedPatients().filter((p) => p.id === 'pt-demo-100') : db.sortedPatients());
const getPatient = (u: User, id: string) => visiblePatients(u).find((p) => p.id === id) ?? fail(404, 'Patient not found.');
const getSession = (id: string) => db.sessions.find((s) => s.id === id) ?? fail(404, 'Session not found.');
const TIER_RANK: Record<Tier, number> = { red: 0, orange: 1, amber: 2, green: 3 };

function trends(patientId: string, range: string) {
  const cfg = { '24h': [96, 15 * 60], '7d': [168, 3600], '30d': [120, 6 * 3600], '90d': [90, 86400] }[range] ?? [168, 3600];
  const [n, step] = cfg;
  const rec = db.demoRecordOf[patientId];
  const prof = PROFILES[rec ?? '100'];
  const hasData = db.sessions.some((s) => s.patient_id === patientId) || patientId === 'pt-1001';
  const r = rng(hash(patientId + range));
  const end = Math.floor(Date.now() / 1000 / step) * step;
  const buckets: TrendBucket[] = [];
  const gaps: { start: string; end: string }[] = [];
  let gapStart: number | null = null;
  // two not-worn periods so that gaps are visible in every range
  const holes = [[Math.floor(n * 0.22), Math.floor(n * 0.31)], [Math.floor(n * 0.68), Math.floor(n * 0.72)]];
  for (let i = 0; i < n; i++) {
    const t = end - (n - 1 - i) * step;
    const hole = !hasData || holes.some(([a, b]) => i >= a && i < b);
    if (hole) {
      if (gapStart === null) gapStart = t;
      buckets.push({ t: new Date(t * 1000).toISOString(), hr_bpm: null, sdnn_ms: null, rmssd_ms: null, s_burden_pct: null, v_burden_pct: null, risk_score: null, findings: [] });
      continue;
    }
    if (gapStart !== null) { gaps.push({ start: new Date(gapStart * 1000).toISOString(), end: new Date(t * 1000).toISOString() }); gapStart = null; }
    const drift = i / n;
    const circ = Math.sin((t / 86400) * 2 * Math.PI);
    const v = Math.max(0, prof.v * 100 * (0.35 + 0.9 * drift) * (0.7 + 0.6 * r()));
    const s = Math.max(0, prof.s * 100 * (0.6 + 0.8 * r()));
    const risk = Math.min(100, Math.round(v * 2.6 + s * 2 + (prof.couplet > 0.1 ? 12 : 0) + 4 * r()));
    buckets.push({
      t: new Date(t * 1000).toISOString(),
      hr_bpm: Math.round((prof.hr + 7 * circ + 6 * (r() - 0.5)) * 10) / 10,
      sdnn_ms: Math.round((48 - 14 * drift * (prof.v > 0.1 ? 1 : 0.2) + 10 * (r() - 0.5)) * 10) / 10,
      rmssd_ms: Math.round((31 - 6 * drift * (prof.v > 0.1 ? 1 : 0.2) + 8 * (r() - 0.5)) * 10) / 10,
      s_burden_pct: Math.round(s * 10) / 10,
      v_burden_pct: Math.round(v * 10) / 10,
      risk_score: risk,
      findings: v > 10 ? [{ rule_id: 'V_BURDEN', label: 'V burden above 10%', severity: 'orange', count: 1 }] : [],
    });
  }
  if (gapStart !== null) gaps.push({ start: new Date(gapStart * 1000).toISOString(), end: new Date((end + step) * 1000).toISOString() });
  return { range, bucket_s: step, buckets, gaps };
}

function csv(sessionId: string): string {
  const s = getSession(sessionId);
  const { beats } = db.recordingOf(s);
  const head = `# CardioSense AI session export (MOCK DATA)\n# ${DISCLAIMER}\n# session ${s.id} model ${db.MODEL_VERSION} rules ${db.RULE_VERSION}\n`;
  return `${head}sample,time_s,label,confidence,p_N,p_S,p_V,p_F,truth\n${beats.map((b) => `${b.sample},${b.time_s},${b.label},${b.confidence},${b.probs.N},${b.probs.S},${b.probs.V},${b.probs.F},${b.truth ?? ''}`).join('\n')}\n`;
}

/** A tiny valid one-page PDF so the download path can be exercised. */
function pdf(lines: string[]): Uint8Array {
  const esc = (s: string) => s.replace(/[^\x20-\x7E]/g, '-').replace(/([\\()])/g, '\\$1');
  const content = `BT /F1 11 Tf 50 790 Td 15 TL ${lines.map((l) => `(${esc(l)}) Tj T*`).join(' ')} ET`;
  const objs = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
    `<< /Length ${content.length} >>\nstream\n${content}\nendstream`,
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
  ];
  let out = '%PDF-1.4\n';
  const offs: number[] = [];
  objs.forEach((o, i) => { offs.push(out.length); out += `${i + 1} 0 obj\n${o}\nendobj\n`; });
  const xref = out.length;
  out += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n${offs.map((o) => `${String(o).padStart(10, '0')} 00000 n \n`).join('')}`;
  out += `trailer\n<< /Size ${objs.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return new TextEncoder().encode(out);
}

function alertAction(user: User, id: string, state: AlertState, action: string, note: unknown): Alert {
  need(user, 'clinician', 'org_admin');
  const a = db.alerts.find((x) => x.id === id) ?? fail(404, 'Alert not found.');
  if (a.state === 'closed') fail(409, 'This alert is already closed.');
  a.state = state;
  a.updated_at = new Date().toISOString();
  if (typeof note === 'string' && note) a.note = note;
  if (state === 'acknowledged') a.acknowledged_by = user.email;
  db.alertHistory.get(id)?.push({ at: a.updated_at, action, actor: user.email, note: typeof note === 'string' && note ? note : null });
  db.log(user.email, `alert.${action}`, `alert ${id}`, { note: note ?? null });
  db.refreshPatients();
  return db.withExtras(a);
}

const META = {
  model_version: db.MODEL_VERSION,
  rule_version: db.RULE_VERSION,
  model_loaded: true,
  disclaimer: DISCLAIMER,
  model: {
    model_version: db.MODEL_VERSION,
    stand_in: true,
    stand_in_note: 'MOCK DATA from the frontend dev mock (VITE_MOCK=1). There is no model behind this page; every number below is a layout placeholder, not a measurement.',
    architecture: 'none (frontend mock)',
    classes: ['N', 'S', 'V', 'F'],
    unknown_threshold: 0.5,
    evaluation_note: 'Placeholder values so the page layout can be reviewed. Run against the real backend to see measured, patient-wise results.',
    evaluation: {
      stand_in: true,
      protocol: 'placeholder — patient-wise: trained on MIT-BIH DS1, tested on DS2 (22 unseen records)',
      ds2: {
        ensemble: { acc: 0, macroF1: 0, N_Se: 0, 'N_+P': 0, S_Se: 0, 'S_+P': 0, V_Se: 0, 'V_+P': 0, F_Se: 0, 'F_+P': 0 },
        confusion_matrix: [[0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]],
        confusion_matrix_axes: 'rows = true N,S,V,F; columns = predicted N,S,V,F',
        unknown_threshold_table: [{ threshold: 0.5, coverage: 0, acc_kept: 0, acc_flagged_unknown: null }],
      },
    },
    limitations: [
      'Mock mode: beat labels come from a scripted generator, not from a model.',
      'Single-lead only; not for ischaemia or 12-lead interpretation.',
      'Not validated on AD8232 recordings; research and demonstration use only, not a medical device.',
    ],
  },
};

const routes: [string, RegExp, Handler][] = [
  ['GET', /^\/auth\/me$/, ({ user }) => pub(user)],
  ['POST', /^\/auth\/logout$/, ({ body }) => { refreshTokens.delete(String(body.refresh_token)); return {}; }],
  ['POST', /^\/auth\/mfa\/enrol$/, () => ({ secret: 'MOCKSECRETBASE32', otpauth_url: 'otpauth://totp/CardioSense:mock?secret=MOCKSECRETBASE32&issuer=CardioSense' })],
  ['POST', /^\/auth\/mfa\/verify$/, ({ user, body }) => { if (!/^\d{6}$/.test(String(body.otp))) fail(400, 'Invalid code.'); const u = db.users.find((x) => x.id === user.id); if (u) u.mfa_enabled = true; return { mfa_enabled: true }; }],

  ['GET', /^\/patients$/, ({ user, q }) => {
    const s = (q.get('q') ?? '').toLowerCase();
    return visiblePatients(user).filter((p) => !s || p.name.toLowerCase().includes(s) || (p.mrn ?? '').toLowerCase().includes(s));
  }],
  ['POST', /^\/patients$/, ({ user, body }) => {
    need(user, 'clinician', 'org_admin');
    if (!body.name || typeof body.name !== 'string') fail(422, 'Name is required.');
    if (body.consent !== true) fail(422, 'Consent must be recorded before a patient can be created for streaming.');
    const p = {
      id: db.newId('pt'), name: String(body.name), mrn: (body.mrn as string) || null, dob: (body.dob as string) || null, sex: (body.sex as string) || null,
      notes: (body.notes as string) || null, consent: true, is_demo: false, risk_score: null, risk_tier: null, last_event: null,
      signal_status: 'offline', active_session_id: null, device_id: null, open_alerts: 0,
    };
    db.patients.push(p);
    db.log(user.email, 'patient.create', `patient ${p.id}`);
    return p;
  }],
  ['GET', /^\/patients\/([^/]+)$/, ({ user, m }) => getPatient(user, m[1])],
  ['PATCH', /^\/patients\/([^/]+)$/, ({ user, m, body }) => { need(user, 'clinician', 'org_admin'); const p = getPatient(user, m[1]); Object.assign(p, body); db.log(user.email, 'patient.update', `patient ${p.id}`, body); return p; }],
  ['GET', /^\/patients\/([^/]+)\/notes$/, ({ user, m }) => { getPatient(user, m[1]); return db.notes.get(m[1]) ?? []; }],
  ['POST', /^\/patients\/([^/]+)\/notes$/, ({ user, m, body }) => {
    getPatient(user, m[1]);
    const n = { id: db.newId('note'), text: String(body.text ?? ''), kind: String(body.kind ?? 'note'), author: user.name, created_at: new Date().toISOString() };
    if (!n.text.trim()) fail(422, 'Note text is required.');
    db.notes.set(m[1], [n, ...(db.notes.get(m[1]) ?? [])]);
    db.log(user.email, 'patient.note', `patient ${m[1]}`);
    return n;
  }],
  ['GET', /^\/patients\/([^/]+)\/sessions$/, ({ user, m }) => { getPatient(user, m[1]); return db.sessions.filter((s) => s.patient_id === m[1]).map(db.summarise); }],
  ['GET', /^\/patients\/([^/]+)\/trends$/, ({ user, m, q }) => { getPatient(user, m[1]); return trends(m[1], q.get('range') ?? '7d'); }],
  ['GET', /^\/patients\/([^/]+)\/insights$/, ({ user, m }) => { getPatient(user, m[1]); return db.insights.get(m[1]) ?? []; }],

  ['GET', /^\/sessions\/([^/]+)$/, ({ m }) => db.summarise(getSession(m[1]))],
  ['GET', /^\/sessions\/([^/]+)\/beats$/, ({ m, q }) => {
    const s = getSession(m[1]);
    const from = Number(q.get('from') ?? 0), to = Number(q.get('to') ?? Infinity);
    return db.recordingOf(s).beats.filter((b) => b.time_s >= from && b.time_s <= to);
  }],
  ['GET', /^\/sessions\/([^/]+)\/signal$/, ({ m, q }) => {
    const s = getSession(m[1]);
    const from = Math.max(0, Number(q.get('from') ?? 0));
    const to = Math.min(s.duration_s ?? 0, Number(q.get('to') ?? Infinity));
    const { mv } = db.recordingOf(s);
    const t0 = Math.round(from * s.fs);
    return { fs: s.fs, t0, mv: mv.slice(t0, Math.round(to * s.fs)) };
  }],
  ['GET', /^\/sessions\/([^/]+)\/events$/, ({ m }) => {
    const s = getSession(m[1]);
    const ev: object[] = [];
    db.windowsOf(s).forEach((w) => w.findings.forEach((f, i) => ev.push({ id: `${w.id}-f${i}`, kind: 'finding', ...f })));
    db.alerts.filter((a) => a.session_id === s.id).forEach((a) => ev.push({
      id: a.id, kind: 'alert', alert_id: a.id, rule_id: a.rule_id, title: a.title, tier: a.tier, start_s: a.evidence.start_s, end_s: a.evidence.end_s, detail: a.detail, created_at: a.created_at,
    }));
    return ev;
  }],

  ['GET', /^\/alerts$/, ({ user, q }) => {
    const vis = new Set(visiblePatients(user).map((p) => p.id));
    return db.alerts
      .filter((a) => vis.has(a.patient_id) && (!q.get('state') || a.state === q.get('state')) && (!q.get('tier') || a.tier === q.get('tier')) && (!q.get('patient_id') || a.patient_id === q.get('patient_id')))
      .sort((a, b) => (TIER_RANK[a.tier] - TIER_RANK[b.tier]) || b.created_at.localeCompare(a.created_at))
      .map(db.withExtras);
  }],
  ['GET', /^\/alerts\/([^/]+)$/, ({ m }) => db.withExtras(db.alerts.find((a) => a.id === m[1]) ?? fail(404, 'Alert not found.'))],
  ['POST', /^\/alerts\/([^/]+)\/ack$/, ({ user, m, body }) => alertAction(user, m[1], 'acknowledged', 'acknowledge', body.note)],
  ['POST', /^\/alerts\/([^/]+)\/escalate$/, ({ user, m, body }) => alertAction(user, m[1], 'escalated', 'escalate', body.note)],
  ['POST', /^\/alerts\/([^/]+)\/close$/, ({ user, m, body }) => alertAction(user, m[1], 'closed', 'close', body.note)],

  ['POST', /^\/copilot\/query$/, async ({ user, body }) => {
    need(user, 'clinician', 'org_admin');
    getPatient(user, String(body.patient_id));
    await new Promise((r) => setTimeout(r, 700));
    return db.makeInsight(String(body.patient_id), typeof body.question === 'string' && body.question ? body.question : undefined);
  }],

  ['GET', /^\/demo\/records$/, () => Object.entries(PROFILES).map(([record, p]) => ({ record, dataset: 'MIT-BIH', description: p.note, duration_s: 1805, fs: 360 }))],
  ['POST', /^\/demo\/replay$/, ({ user, body }) => {
    need(user, 'clinician', 'org_admin', 'system_admin');
    const record = String(body.record ?? '208');
    if (!PROFILES[record]) fail(404, `Unknown record ${record}.`);
    const pid = typeof body.patient_id === 'string' && body.patient_id ? body.patient_id : demoPatientForRecord(record);
    const p = db.patients.find((x) => x.id === pid) ?? fail(404, 'Patient not found.');
    if (!p.is_demo) fail(400, 'Replay can only be started for demo patients.');
    const s = startStream(pid, record, String(body.dataset ?? 'MIT-BIH'), Math.max(0.25, Math.min(8, Number(body.speed ?? 1) || 1)));
    db.log(user.email, 'demo.replay', `patient ${pid}`, { record });
    return { patient_id: pid, device_id: s.session.device_id, record, status: 'started' };
  }],
  ['POST', /^\/demo\/stop$/, ({ user, body }) => { need(user, 'clinician', 'org_admin', 'system_admin'); stopStream(typeof body.patient_id === 'string' ? body.patient_id : undefined); return { status: 'stopped' }; }],

  ['GET', /^\/devices$/, ({ user }) => { need(user, 'technician', 'org_admin', 'system_admin', 'clinician'); return db.devices.map((d) => ({ ...d, patient_name: db.patients.find((p) => p.id === d.patient_id)?.name ?? null, status: d.patient_id && activeStream(d.patient_id) ? 'streaming' : 'offline' })); }],
  ['POST', /^\/devices$/, ({ user, body }) => {
    need(user, 'technician', 'org_admin', 'system_admin');
    const device_id = String(body.device_id || db.newId('dev'));
    const code = String(Math.floor(100000 + Math.random() * 900000));
    const d = { device_id, name: (body.name as string) || device_id, patient_id: (body.patient_id as string) || null, firmware: null, paired: false, last_seen: null };
    db.devices.push(d);
    db.pairing.set(code, { device_id, expires: Date.now() + 600_000 });
    db.log(user.email, 'device.create', `device ${device_id}`);
    return { ...d, pairing_code: code, pairing_expires_at: new Date(Date.now() + 600_000).toISOString() };
  }],
  ['POST', /^\/devices\/pair$/, ({ user, body }) => {
    const code = String(body.pairing_code ?? '');
    const p = db.pairing.get(code);
    if (!p || p.expires < Date.now()) fail(400, 'Pairing code is invalid or has expired.');
    db.pairing.delete(code);
    const d = db.devices.find((x) => x.device_id === p!.device_id);
    if (d) d.paired = true;
    db.log(user.email, 'device.pair', `device ${p!.device_id}`);
    return { device_id: p!.device_id, device_key: `mock-key-${Math.random().toString(36).slice(2, 12)}` };
  }],

  ['GET', /^\/audit$/, ({ user, q }) => {
    need(user, 'org_admin', 'system_admin');
    const actor = (q.get('actor') ?? '').toLowerCase(), action = (q.get('action') ?? '').toLowerCase();
    const from = q.get('from'), to = q.get('to');
    return db.audit.filter((a) => (!actor || (a.actor_email ?? '').toLowerCase().includes(actor)) && (!action || a.action.toLowerCase().includes(action))
      && (!from || (a.at ?? '') >= from) && (!to || (a.at ?? '') <= to));
  }],
  ['GET', /^\/admin\/users$/, ({ user }) => { need(user, 'org_admin', 'system_admin'); return db.users.map(pub); }],
  ['PATCH', /^\/admin\/users\/([^/]+)$/, ({ user, m, body }) => {
    need(user, 'org_admin', 'system_admin');
    const u = db.users.find((x) => x.id === m[1]) ?? fail(404, 'User not found.');
    if (u.id === user.id && body.role && body.role !== u.role) fail(400, 'You cannot change your own role.');
    if (typeof body.role === 'string') u.role = body.role as Role;
    if (typeof body.active === 'boolean') u.active = body.active;
    db.log(user.email, 'user.update', `user ${u.id}`, body);
    return pub(u);
  }],
  ['GET', /^\/admin\/rules$/, ({ user }) => { need(user, 'org_admin', 'system_admin', 'clinician'); return db.rules; }],
  ['PUT', /^\/admin\/rules$/, ({ user, body }) => {
    need(user, 'org_admin', 'system_admin');
    const r = db.setRules((body.thresholds ?? {}) as Record<string, unknown>, user.email);
    db.log(user.email, 'rules.update', 'rules', { version: r.version });
    return r;
  }],
  ['GET', /^\/meta$/, () => META],
];

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

export async function mockFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const url = new URL(input, 'http://mock.local');
  const path = url.pathname.replace(/^\/v1/, '');
  const method = (init.method ?? 'GET').toUpperCase();
  let body: Record<string, unknown> = {};
  if (typeof init.body === 'string' && init.body) { try { body = JSON.parse(init.body) as Record<string, unknown>; } catch { /* ignore */ } }
  await new Promise((r) => setTimeout(r, 60 + Math.random() * 120)); // a little latency so loading states are visible
  if (init.signal?.aborted) throw new DOMException('Aborted', 'AbortError');

  try {
    // public endpoints
    if (path === '/health') return json(200, { status: 'ok', mock: true });
    if (method === 'POST' && path === '/auth/login') {
      const u = db.users.find((x) => x.email.toLowerCase() === String(body.email ?? '').toLowerCase());
      if (!u || u.password !== body.password) fail(401, 'Incorrect email or password.');
      if (u!.active === false) fail(403, 'This account is disabled.');
      if (u!.mfa_enabled && !body.otp) return json(401, { detail: 'OTP required', mfa_required: true });
      if (u!.mfa_enabled && !/^\d{6}$/.test(String(body.otp))) fail(401, 'Invalid OTP code.');
      db.log(u!.email, 'auth.login', `user ${u!.id}`);
      return json(200, issue(u!));
    }
    if (method === 'POST' && path === '/auth/refresh') {
      const tok = String(body.refresh_token ?? '');
      const uid = refreshTokens.get(tok);
      const u = db.users.find((x) => x.id === uid);
      if (!u) fail(401, 'Refresh token is invalid or has been used.');
      refreshTokens.delete(tok); // rotating
      return json(200, issue(u!));
    }

    const headers = new Headers(init.headers);
    const user = userFromAuth(headers.get('Authorization'));
    if (!user) return json(401, { detail: 'Not authenticated.' });

    // binary endpoints
    let m = /^\/reports\/([^/]+)\.pdf$/.exec(path);
    if (m && method === 'GET') {
      const p = getPatient(user, m[1]);
      const bytes = pdf([
        'CardioSense AI - patient report (MOCK DATA)', '', `Patient: ${p.name}   MRN: ${p.mrn ?? '-'}`, `Range: ${url.searchParams.get('range') ?? '7d'}`,
        `Generated: ${new Date().toISOString()}`, `Model: ${db.MODEL_VERSION}   Rules: ${db.rules.version}`, '',
        'This file is produced by the frontend dev mock. The real report comes from the backend.', '', DISCLAIMER,
      ]);
      db.log(user.email, 'report.pdf', `patient ${p.id}`);
      return new Response(new Blob([bytes as BlobPart]), { status: 200, headers: { 'Content-Type': 'application/pdf', 'Content-Disposition': `attachment; filename="cardiosense-${p.id}.pdf"` } });
    }
    m = /^\/sessions\/([^/]+)\/export\.csv$/.exec(path);
    if (m && method === 'GET') {
      return new Response(csv(m[1]), { status: 200, headers: { 'Content-Type': 'text/csv', 'Content-Disposition': `attachment; filename="session-${m[1]}.csv"` } });
    }

    for (const [meth, re, h] of routes) {
      if (meth !== method) continue;
      const mm = re.exec(path);
      if (mm) return json(200, await h({ m: mm, q: url.searchParams, body, user }));
    }
    return json(404, { detail: `Mock has no route for ${method} ${path}` });
  } catch (e) {
    if (e instanceof HttpError) return json(e.status, { detail: e.message });
    return json(500, { detail: e instanceof Error ? e.message : 'mock error' });
  }
}
