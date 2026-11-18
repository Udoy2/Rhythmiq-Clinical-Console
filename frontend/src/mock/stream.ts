/** DEV MOCK ONLY — synthetic `WS /v1/live/{patient_id}` feed. */
import type { SocketLike } from '../api/transport';
import type { Beat, RhythmWindow } from '../api/types';
import {
  addAlert, addSession, alerts, demoRecordOf, evidenceFor, FS, MODEL_VERSION, newId, patients, refreshPatients, RULE_VERSION,
  sessions, withExtras, type StoredSession,
} from './db';
import { EcgSynth, hash, mockLabel, mockWindow, profileFor } from './synth';

const sockets = new Map<string, Set<MockSocket>>();
const streams = new Map<string, MockStream>();
let blockedUntil = 0;

function broadcast(patientId: string, msg: object) {
  const data = JSON.stringify(msg);
  sockets.get(patientId)?.forEach((s) => s.deliver(data));
}

class MockStream {
  readonly session: StoredSession;
  private synth: EcgSynth;
  private timer: ReturnType<typeof setInterval>;
  private beats: Beat[] = [];
  private nextWindowEnd = 10;
  private carry = 0;
  private skipped = 0; // samples skipped by injected gaps
  private leadOffUntil = 0; // stream seconds
  private orangeStreak = new Map<string, number>();
  private fired = new Set<string>();
  private lastWall = Date.now();

  constructor(readonly patientId: string, readonly record: string, readonly dataset: string, public speed: number) {
    const id = newId('ses');
    this.session = addSession(patientId, record, 0, 0, { id, active: true, ended_at: null, seed: hash(id), dataset });
    this.synth = new EcgSynth(profileFor(record), FS, this.session.seed);
    const p = patients.find((x) => x.id === patientId);
    if (p) { p.signal_status = 'streaming'; p.active_session_id = id; }
    broadcast(patientId, this.sessionMsg(true));
    broadcast(patientId, { type: 'status', stream: 'connected', last_sample_time: new Date().toISOString() });
    this.timer = setInterval(() => this.tick(), 100);
  }

  sessionMsg(active: boolean) {
    const s = this.session;
    return {
      type: 'session', session_id: s.id, patient_id: s.patient_id, device_id: s.device_id, fs: FS, source: 'replay',
      record: this.record, dataset: this.dataset, started_at: s.started_at, model_version: MODEL_VERSION,
      rule_version: RULE_VERSION, model_loaded: true, active,
    };
  }

  private get streamS() { return (this.synth.position + this.skipped) / FS; }

  private tick() {
    const now = Date.now();
    const dt = Math.min(1000, now - this.lastWall); // background tabs throttle timers; cap the catch-up
    this.lastWall = now;
    const want = (FS * this.speed * dt) / 1000 + this.carry;
    const n = Math.floor(want);
    this.carry = want - n;
    if (n <= 0) return;
    const out = this.synth.next(n);
    const leadOff = this.streamS < this.leadOffUntil;
    const mv = leadOff ? out.mv.map(() => Math.round((Math.random() - 0.5) * 60) / 1000) : out.mv;
    broadcast(this.patientId, { type: 'signal', t0: out.t0 + this.skipped, fs: FS, mv, lead_off: leadOff });
    for (const b of this.synth.takeBeats()) {
      if (leadOff) continue;
      const beat = mockLabel(b.sample + this.skipped, b.cls, FS, MODEL_VERSION, this.session.seed);
      // the live toy model and the stored-session rendering must agree: label by the un-shifted sample
      const stored = mockLabel(b.sample, b.cls, FS, MODEL_VERSION, this.session.seed);
      const msg = { ...stored, sample: beat.sample, time_s: beat.time_s };
      this.beats.push(msg);
      broadcast(this.patientId, { type: 'beat', ...msg });
    }
    while (this.streamS >= this.nextWindowEnd + 1.2) this.emitWindow();
    this.session.duration_s = Math.floor(this.synth.position / FS);
  }

  private emitWindow() {
    const e = this.nextWindowEnd;
    this.nextWindowEnd += 10;
    const inWin = this.beats.filter((b) => b.time_s >= e - 10 && b.time_s < e);
    const lastMin = this.beats.filter((b) => b.time_s >= e - 60 && b.time_s < e);
    this.beats = this.beats.filter((b) => b.time_s >= e - 70);
    const suppressed = e - 10 < this.leadOffUntil;
    const w: RhythmWindow & { id: string } = mockWindow(`${this.session.id}-w${e / 10}`, e - 10, e, inWin, lastMin, RULE_VERSION);
    if (suppressed) { w.sqi = 0.2; w.findings = []; w.risk = { ...w.risk, factors: [] }; }
    broadcast(this.patientId, { type: 'window', ...w });
    const p = patients.find((x) => x.id === this.patientId);
    if (p) { p.risk_score = w.risk.score; p.risk_tier = w.risk.tier; }
    if (suppressed) return; // low quality suppresses clinical alerts
    // Orange findings need 2 consecutive windows; one alert per rule per session.
    for (const rule of ['V_RUN', 'V_BURDEN']) {
      const f = w.findings.find((x) => x.rule_id === rule && x.severity === 'orange');
      const streak = f ? (this.orangeStreak.get(rule) ?? 0) + 1 : 0;
      this.orangeStreak.set(rule, streak);
      if (f && streak >= 2 && !this.fired.has(rule) && this.skipped === 0) {
        this.fired.add(rule);
        this.session.duration_s = Math.floor(this.synth.position / FS);
        const a = addAlert({
          patient_id: this.patientId, session_id: this.session.id, rule_id: rule, tier: 'orange',
          title: rule === 'V_RUN' ? 'Non-sustained V run' : 'V burden above 10%',
          detail: `${f.detail} in 2 consecutive windows.`, created_at: new Date().toISOString(),
          evidence: evidenceFor({ ...this.session, active: false, duration_s: e }, e - 10, e),
        });
        refreshPatients();
        broadcast(this.patientId, { type: 'alert', alert: withExtras(a) });
      }
    }
  }

  leadOff(seconds: number) {
    this.leadOffUntil = this.streamS + seconds * this.speed;
    broadcast(this.patientId, { type: 'quality', state: 'lead_off', detail: 'Electrode contact lost (mock)', sqi: 0.05 });
    const p = patients.find((x) => x.id === this.patientId);
    if (p) p.signal_status = 'lead_off';
    setTimeout(() => {
      if (streams.get(this.patientId) !== this) return;
      broadcast(this.patientId, { type: 'quality', state: 'ok', detail: '', sqi: 0.93 });
      if (p) p.signal_status = 'streaming';
    }, seconds * 1000);
  }

  lowSqi(seconds: number) {
    broadcast(this.patientId, { type: 'quality', state: 'low_sqi', detail: 'Motion artefact (mock)', sqi: 0.31 });
    setTimeout(() => { if (streams.get(this.patientId) === this) broadcast(this.patientId, { type: 'quality', state: 'ok', detail: '', sqi: 0.9 }); }, seconds * 1000);
  }

  gap(seconds: number) {
    this.skipped += Math.round(seconds * FS);
    broadcast(this.patientId, { type: 'quality', state: 'gap', detail: `${seconds.toFixed(1)} s of samples missing (mock)`, sqi: 0.5 });
    setTimeout(() => { if (streams.get(this.patientId) === this) broadcast(this.patientId, { type: 'quality', state: 'ok', detail: '', sqi: 0.92 }); }, 4000);
  }

  stop() {
    clearInterval(this.timer);
    const s = this.session;
    s.active = false;
    s.duration_s = Math.floor(this.synth.position / FS);
    s.ended_at = new Date().toISOString();
    if ((s.duration_s ?? 0) < 10) {
      const i = sessions.indexOf(s);
      if (i >= 0 && !alerts.some((a) => a.session_id === s.id)) sessions.splice(i, 1);
    }
    const p = patients.find((x) => x.id === this.patientId);
    if (p) { p.signal_status = 'offline'; p.active_session_id = null; }
    broadcast(this.patientId, this.sessionMsg(false));
    broadcast(this.patientId, { type: 'status', stream: 'disconnected', last_sample_time: new Date().toISOString() });
    refreshPatients();
  }
}

export function startStream(patientId: string, record: string, dataset = 'MIT-BIH', speed = 1): MockStream {
  streams.get(patientId)?.stop();
  const s = new MockStream(patientId, record, dataset, speed);
  streams.set(patientId, s);
  return s;
}
export function stopStream(patientId?: string) {
  for (const [pid, s] of [...streams]) {
    if (patientId && pid !== patientId) continue;
    s.stop();
    streams.delete(pid);
  }
}
export const activeStream = (patientId: string) => streams.get(patientId);
export const demoPatientForRecord = (record: string) =>
  Object.entries(demoRecordOf).find(([, r]) => r === record)?.[0] ?? 'pt-demo-208';

export class MockSocket implements SocketLike {
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: { code?: number; reason?: string }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  private open = false;
  private closed = false;
  private patientId: string;

  constructor(url: string) {
    const m = /\/live\/([^/?]+)/.exec(url);
    this.patientId = m ? decodeURIComponent(m[1]) : '';
    setTimeout(() => this.connect(), 120);
  }

  private connect() {
    if (this.closed) return;
    if (Date.now() < blockedUntil || !patients.some((p) => p.id === this.patientId)) {
      this.closed = true;
      this.onclose?.({ code: 1006, reason: 'mock: unreachable' });
      return;
    }
    this.open = true;
    let set = sockets.get(this.patientId);
    if (!set) sockets.set(this.patientId, (set = new Set()));
    set.add(this);
    this.onopen?.({});
    const st = streams.get(this.patientId);
    if (st) {
      this.deliver(JSON.stringify(st.sessionMsg(true)));
      this.deliver(JSON.stringify({ type: 'status', stream: 'connected', last_sample_time: new Date().toISOString() }));
    } else {
      this.deliver(JSON.stringify({ type: 'status', stream: 'disconnected', last_sample_time: null }));
    }
  }

  deliver(data: string) {
    if (this.open && !this.closed) this.onmessage?.({ data });
  }

  close(code = 1000, reason = '') {
    if (this.closed) return;
    this.closed = true;
    this.open = false;
    sockets.get(this.patientId)?.delete(this);
    this.onclose?.({ code, reason });
  }
}

/** Mock-only fault injection, used by the "Mock faults" strip in the demo controls. */
export const mockControls = {
  leadOff: (patientId: string, seconds = 6) => streams.get(patientId)?.leadOff(seconds),
  lowSqi: (patientId: string, seconds = 6) => streams.get(patientId)?.lowSqi(seconds),
  gap: (patientId: string, seconds = 1.5) => streams.get(patientId)?.gap(seconds),
  dropConnection: (seconds = 6) => {
    blockedUntil = Date.now() + seconds * 1000;
    sockets.forEach((set) => [...set].forEach((s) => s.close(1006, 'mock: dropped')));
  },
};
export type MockControls = typeof mockControls;
