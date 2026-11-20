import { describe, expect, it } from 'vitest';
import type { Alert, RhythmWindow, SessionMsg, WindowMsg } from '../api/types';
import { LiveFeed } from './liveFeed';
import {
  alertsSuppressed, backoffMs, initialLiveState, MAX_WINDOWS, parseLiveMessage, reduceLive, type LiveState,
} from './liveReducer';
import { PlayoutClock, ReplayClock } from './playout';

const session = (id = 's1', extra: Partial<SessionMsg> = {}): SessionMsg => ({
  type: 'session', session_id: id, patient_id: 'p1', device_id: 'dev-demo-208', fs: 360, source: 'replay',
  record: '208', dataset: 'MIT-BIH', started_at: '2026-01-01T00:00:00Z', model_version: 'm1',
  rule_version: 'rules-1.0.0', model_loaded: true, active: true, ...extra,
});
const win = (end: number, extra: Partial<RhythmWindow> = {}): WindowMsg => ({
  type: 'window', id: `w${end}`, start_s: end - 10, end_s: end, hr_bpm: 78.5, findings: [], s_burden_pct: 0,
  v_burden_pct: 12.5, sqi: 0.93, sdnn_ms: 41, rmssd_ms: 28, beat_counts: { N: 10, S: 0, V: 2, F: 0, Unknown: 0 },
  rule_version: 'rules-1.0.0', risk: { score: 62, tier: 'orange', factors: [] }, ...extra,
});
const alert = (id: string, extra: Partial<Alert> = {}): Alert => ({
  id, patient_id: 'p1', session_id: 's1', rule_id: 'V_RUN', tier: 'orange', title: 'Non-sustained V run', detail: '',
  state: 'open', created_at: '2026-01-01T00:00:10Z', updated_at: '2026-01-01T00:00:10Z', acknowledged_by: null, note: null,
  evidence: { start_s: 0, end_s: 10, fs: 250, strip_mv: [], beats: [], rule_version: 'r', model_version: 'm' }, ...extra,
});
const feed = (s: LiveState, ...msgs: object[]) =>
  msgs.reduce<LiveState>((acc, m) => reduceLive(acc, { kind: 'msg', msg: m as { type: string } }), s);

describe('reduceLive', () => {
  it('stores the session and the latest window', () => {
    const s = feed(initialLiveState, session(), win(10), win(20, { hr_bpm: 91 }));
    expect(s.session?.record).toBe('208');
    expect(s.window?.hr_bpm).toBe(91);
    expect(s.windows.length).toBe(2);
    expect('type' in (s.window as object)).toBe(false);
  });

  it('starts per-session state over when a different session begins, keeping alerts', () => {
    let s = feed(initialLiveState, session('s1'), win(10), { type: 'quality', state: 'low_sqi', sqi: 0.3 },
      { type: 'alert', alert: alert('a1') });
    s = feed(s, session('s2'));
    expect(s.window).toBeNull();
    expect(s.windows).toEqual([]);
    expect(s.quality.state).toBe('ok');
    expect(s.alerts.length).toBe(1);
  });

  it('keeps the last state when the same session ends (active:false)', () => {
    const s = feed(initialLiveState, session('s1'), win(10), session('s1', { active: false }));
    expect(s.session?.active).toBe(false);
    expect(s.window?.end_s).toBe(10);
  });

  it('ignores re-delivered older windows and caps the history', () => {
    let s = feed(initialLiveState, session(), win(10), win(20), win(10));
    expect(s.windows.map((w) => w.end_s)).toEqual([10, 20]);
    for (let i = 3; i < MAX_WINDOWS + 10; i++) s = feed(s, win(i * 10));
    expect(s.windows.length).toBe(MAX_WINDOWS);
    expect(s.window?.end_s).toBe((MAX_WINDOWS + 9) * 10);
  });

  it('queues a toast for a new open alert only, and merges updates', () => {
    let s = feed(initialLiveState, { type: 'alert', alert: alert('a1') });
    expect(s.toastQueue).toEqual(['a1']);
    s = feed(s, { type: 'alert', alert: alert('a1', { state: 'acknowledged', acknowledged_by: 'dr' }) });
    expect(s.alerts.length).toBe(1);
    expect(s.alerts[0].state).toBe('acknowledged');
    expect(s.toastQueue).toEqual(['a1']);
    s = reduceLive(s, { kind: 'toast-shown', id: 'a1' });
    expect(s.toastQueue).toEqual([]);
    s = reduceLive(s, { kind: 'alert-updated', alert: alert('a2') });
    expect(s.toastQueue).toEqual([]); // REST-loaded alerts never toast
  });

  it('orders alerts red first, closed last', () => {
    const s = feed(initialLiveState,
      { type: 'alert', alert: alert('amber', { tier: 'amber' }) },
      { type: 'alert', alert: alert('closedRed', { tier: 'red', state: 'closed' }) },
      { type: 'alert', alert: alert('red', { tier: 'red' }) },
      { type: 'alert', alert: alert('orange', { tier: 'orange' }) });
    expect(s.alerts.map((a) => a.id)).toEqual(['red', 'orange', 'amber', 'closedRed']);
  });

  it('tracks quality and stream status', () => {
    let s = feed(initialLiveState, session(), { type: 'quality', state: 'lead_off', detail: 'RA electrode', sqi: 0.1 });
    expect(s.quality).toEqual({ state: 'lead_off', detail: 'RA electrode', sqi: 0.1 });
    expect(alertsSuppressed(s.quality.state)).toBe(true);
    s = feed(s, { type: 'quality', state: 'ok' });
    expect(alertsSuppressed(s.quality.state)).toBe(false);
    expect(s.session?.active).toBe(true);
    s = feed(s, { type: 'status', stream: 'disconnected', last_sample_time: '2026-01-01T00:01:00Z' });
    expect(s.stream).toBe('disconnected');
    expect(s.lastSampleTime).toBe('2026-01-01T00:01:00Z');
    expect(s.session?.active).toBe(false);
  });

  it('tolerates unknown message types and extra fields', () => {
    const before = feed(initialLiveState, session(), win(10));
    const s = feed(before, { type: 'scalogram', data: [1, 2] }, { ...win(20), brand_new_field: { x: 1 } });
    expect(s.unknownMessages).toBe(1);
    expect(s.window?.end_s).toBe(20);
    expect(s.session).toEqual(before.session);
  });

  it('keeps the last clinical state across a reconnect', () => {
    let s = feed(initialLiveState, session(), win(10));
    s = reduceLive(s, { kind: 'conn', conn: 'reconnecting', attempt: 2, retryAt: 123 });
    expect(s.conn).toBe('reconnecting');
    expect(s.attempt).toBe(2);
    expect(s.retryAt).toBe(123);
    expect(s.window?.end_s).toBe(10);
    s = reduceLive(s, { kind: 'conn', conn: 'open' });
    expect(s.attempt).toBe(0);
    expect(s.retryAt).toBeNull();
    s = feed(s, session()); // same session id after reconnect
    expect(s.window?.end_s).toBe(10);
  });
});

describe('parseLiveMessage / backoff', () => {
  it('parses JSON objects with a type and rejects the rest', () => {
    expect(parseLiveMessage('{"type":"status","stream":"connected"}')).toEqual({ type: 'status', stream: 'connected' });
    expect(parseLiveMessage('{"type":"future","x":1}')).toEqual({ type: 'future', x: 1 });
    expect(parseLiveMessage('not json')).toBeNull();
    expect(parseLiveMessage('[1,2]')).toBeNull();
    expect(parseLiveMessage('{"no_type":1}')).toBeNull();
    expect(parseLiveMessage(new ArrayBuffer(4))).toBeNull();
  });

  it('backs off exponentially up to 10 s', () => {
    const mid = () => 0.5;
    expect(backoffMs(1, mid)).toBe(500);
    expect(backoffMs(2, mid)).toBe(1000);
    expect(backoffMs(4, mid)).toBe(4000);
    expect(backoffMs(20, mid)).toBe(10000);
    expect(backoffMs(3, () => 0)).toBe(1600);
    expect(backoffMs(3, () => 1)).toBe(2400);
  });
});

describe('LiveFeed buffers', () => {
  it('routes signal and beats to the buffers and resets them on a new session', () => {
    const f = new LiveFeed('p1');
    f.handle(session('s1'));
    f.handle({ type: 'signal', t0: 0, fs: 360, mv: new Array(36).fill(0.1), lead_off: false });
    f.handle({ type: 'signal', t0: 72, fs: 360, mv: new Array(36).fill(0.2), lead_off: false }); // 36-sample gap
    f.handle({ type: 'beat', sample: 90, time_s: 0.25, label: 'V', confidence: 0.97, probs: { N: 0.01, S: 0, V: 0.97, F: 0.02 }, model_version: 'm1', truth: 'V' });
    f.handle({ type: 'beat', sample: 20, time_s: 0.0556, label: 'weird', confidence: 0.5, model_version: 'm1' } as never);
    expect(f.ring.end).toBe(108);
    expect(Number.isNaN(f.ring.get(50))).toBe(true);
    expect(f.beats.items.map((b) => b.label)).toEqual(['Unknown', 'V']);
    expect(f.beats.items[1].t).toBeCloseTo(0.25);
    expect(f.store.getState().session?.session_id).toBe('s1');
    f.handle(session('s2'));
    expect(f.ring.isEmpty).toBe(true);
    expect(f.beats.items.length).toBe(0);
  });

  it('maps t0 from the device rate when the display signal uses another rate', () => {
    const f = new LiveFeed('p1');
    f.handle(session('s1', { fs: 360 }));
    f.handle({ type: 'signal', t0: 3600, fs: 250, mv: new Array(25).fill(0), lead_off: false });
    expect(f.ring.fs).toBe(250);
    expect(f.ring.start).toBe(2500);
    expect(f.ring.endTime).toBeCloseTo(10.1);
  });

  it('does not toast the alerts the server replays right after the socket opens, but toasts later ones', () => {
    const f = new LiveFeed('p1');
    f.dispatch({ kind: 'conn', conn: 'open' });
    // Backlog is decided by created_at against the wall-clock time of the open.
    (f as unknown as { openedAtWall: number }).openedAtWall = Date.parse('2026-01-01T00:00:10Z') + 100;
    f.handle({ type: 'alert', alert: alert('old') });
    expect(f.store.getState().alerts.map((a) => a.id)).toEqual(['old']);
    expect(f.store.getState().toastQueue).toEqual([]);
    f.handle({ type: 'alert', alert: alert('new', { created_at: '2026-01-01T00:00:20Z' }) });
    expect(f.store.getState().toastQueue).toEqual(['new']);
  });

  it('records a permanent refusal and clears it on the next successful open', () => {
    let s = reduceLive(initialLiveState, { kind: 'conn', conn: 'closed', denied: 'not permitted for this patient' });
    expect(s.denied).toBe('not permitted for this patient');
    s = reduceLive(s, { kind: 'conn', conn: 'open' });
    expect(s.denied).toBeNull();
  });

  it('raises a lead-off quality state from frame flags when no quality message arrives', () => {
    const f = new LiveFeed('p1');
    f.handle(session());
    f.handle({ type: 'signal', t0: 0, fs: 360, mv: [0, 0], lead_off: true });
    expect(f.store.getState().quality.state).toBe('lead_off');
    f.handle({ type: 'signal', t0: 2, fs: 360, mv: [0, 0], lead_off: false });
    expect(f.store.getState().quality.state).toBe('ok');
  });
});

describe('clocks', () => {
  it('PlayoutClock trails the data by the jitter delay, is monotonic and never passes the head', () => {
    const c = new PlayoutClock(0.2);
    let now = 0, head = 0, prev = -1;
    for (let frame = 0; frame < 600; frame++) { // 10 s at 60 fps
      now += 1000 / 60;
      if (frame % 6 === 0) { head += 0.1; c.onData(head, now + (frame % 12 === 0 ? 15 : 0)); }
      const p = c.now(now);
      expect(p).toBeGreaterThanOrEqual(prev);
      expect(p).toBeLessThanOrEqual(head + 1e-9);
      prev = p;
    }
    expect(head - prev).toBeGreaterThan(0.05);
    expect(head - prev).toBeLessThan(0.45);
    expect(c.streamRate).toBeCloseTo(1, 1);
  });

  it('PlayoutClock follows a 4x replay and holds when data stops', () => {
    const c = new PlayoutClock(0.2);
    let now = 0, head = 0;
    for (let frame = 0; frame < 300; frame++) {
      now += 1000 / 60;
      if (frame % 6 === 0) { head += 0.4; c.onData(head, now); }
      c.now(now);
    }
    expect(c.streamRate).toBeCloseTo(4, 0);
    let p = 0;
    for (let frame = 0; frame < 300; frame++) { now += 1000 / 60; p = c.now(now); }
    expect(p).toBeCloseTo(head, 3);
    expect(c.now(now + 500)).toBeCloseTo(head, 3);
  });

  it('ReplayClock is deterministic: position depends only on elapsed time and speed', () => {
    const c = new ReplayClock(0, 60);
    expect(c.now(1000)).toBe(0);
    c.play(1000);
    expect(c.now(3000)).toBeCloseTo(2);
    c.setSpeed(2, 3000);
    expect(c.now(4000)).toBeCloseTo(4);
    c.pause(4000);
    expect(c.now(9000)).toBeCloseTo(4);
    c.seek(58, 9000);
    c.play(9000);
    expect(c.now(9500)).toBeCloseTo(59);
    expect(c.now(20000)).toBe(60);
    expect(c.playing).toBe(false);
  });
});
