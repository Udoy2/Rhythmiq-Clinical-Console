/**
 * Pure state machine for the low-rate live messages (session, window, alert, quality, status) and the
 * connection lifecycle. High-rate `signal` / `beat` messages bypass React and go to the buffers in liveFeed.ts.
 */
import type {
  Alert, LiveMsg, QualityMsg, QualityState, RhythmWindow, SessionMsg, StatusMsg, WindowMsg,
} from '../api/types';

export type ConnState = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed';

export interface LiveState {
  conn: ConnState;
  /** Set when the server refused the socket for good (close code 4403): no retries. */
  denied: string | null;
  /** Reconnect attempt counter (0 while connected). */
  attempt: number;
  /** Wall-clock ms of the next reconnect attempt, if one is scheduled. */
  retryAt: number | null;
  session: SessionMsg | null;
  /** Latest rhythm window of the current session. */
  window: RhythmWindow | null;
  /** Recent windows of the current session (newest last, capped). */
  windows: RhythmWindow[];
  quality: { state: QualityState; detail: string; sqi: number | null };
  /** Device stream as reported by the server (`status` message). */
  stream: 'connected' | 'disconnected' | 'unknown';
  /** ISO time of the last sample: from `status.last_sample_time`, else the local arrival time of the last signal frame. */
  lastSampleTime: string | null;
  /** Alerts seen on this socket, newest first, keyed by id. */
  alerts: Alert[];
  /** Ids of alerts that arrived as *new* on this socket and have not been toasted yet. */
  toastQueue: string[];
  /** Count of messages with an unrecognised `type` (tolerated and ignored). */
  unknownMessages: number;
  /** Bumped when sample/beat buffers reset without a new session_id (in-place stream restart). */
  bufferEpoch: number;
}

export const initialLiveState: LiveState = {
  conn: 'idle',
  denied: null,
  attempt: 0,
  retryAt: null,
  session: null,
  window: null,
  windows: [],
  quality: { state: 'ok', detail: '', sqi: null },
  stream: 'unknown',
  lastSampleTime: null,
  alerts: [],
  toastQueue: [],
  unknownMessages: 0,
  bufferEpoch: 0,
};

export const MAX_WINDOWS = 60;

export type LiveEvent =
  | { kind: 'msg'; msg: LiveMsg | { type: string } }
  | { kind: 'conn'; conn: ConnState; attempt?: number; retryAt?: number | null; denied?: string | null }
  | { kind: 'sample-time'; iso: string }
  | { kind: 'toast-shown'; id: string }
  | { kind: 'alert-updated'; alert: Alert }
  | { kind: 'buffers-reset' };

const TIER_ORDER = { red: 0, orange: 1, amber: 2, green: 3 } as const;

export function sortAlerts(alerts: Alert[]): Alert[] {
  return [...alerts].sort((a, b) => {
    const ao = a.state === 'closed' ? 1 : 0, bo = b.state === 'closed' ? 1 : 0;
    if (ao !== bo) return ao - bo;
    const t = (TIER_ORDER[a.tier] ?? 9) - (TIER_ORDER[b.tier] ?? 9);
    if (t !== 0) return t;
    return (b.created_at ?? '').localeCompare(a.created_at ?? '');
  });
}

function upsertAlert(state: LiveState, alert: Alert, toast: boolean): LiveState {
  const i = state.alerts.findIndex((a) => a.id === alert.id);
  const isNew = i === -1;
  const alerts = isNew ? [alert, ...state.alerts] : state.alerts.map((a, j) => (j === i ? { ...a, ...alert } : a));
  const toastQueue = toast && isNew && alert.state === 'open' ? [...state.toastQueue, alert.id] : state.toastQueue;
  return { ...state, alerts: sortAlerts(alerts), toastQueue };
}

export function reduceLive(state: LiveState, ev: LiveEvent): LiveState {
  switch (ev.kind) {
    case 'conn':
      return {
        ...state,
        conn: ev.conn,
        attempt: ev.conn === 'open' ? 0 : ev.attempt ?? state.attempt,
        retryAt: ev.conn === 'reconnecting' ? ev.retryAt ?? null : null,
        denied: ev.conn === 'closed' ? ev.denied ?? null : null,
      };
    case 'sample-time':
      return { ...state, lastSampleTime: ev.iso };
    case 'toast-shown':
      return { ...state, toastQueue: state.toastQueue.filter((id) => id !== ev.id) };
    case 'alert-updated':
      return upsertAlert(state, ev.alert, false);
    case 'buffers-reset':
      return { ...state, bufferEpoch: state.bufferEpoch + 1 };
    case 'msg':
      return reduceMsg(state, ev.msg);
  }
}

function reduceMsg(state: LiveState, msg: LiveMsg | { type: string }): LiveState {
  switch (msg.type) {
    case 'session': {
      const m = msg as SessionMsg;
      const changed = !state.session || state.session.session_id !== m.session_id;
      if (!changed) return { ...state, session: { ...state.session, ...m } };
      // A different session: per-session state starts over; alerts are kept (they belong to the patient).
      return {
        ...state,
        session: m,
        window: null,
        windows: [],
        quality: { state: 'ok', detail: '', sqi: null },
      };
    }
    case 'window': {
      const m = msg as WindowMsg;
      const { type: _t, ...w } = m;
      void _t;
      const last = state.windows[state.windows.length - 1];
      // ignore re-delivery of an older window after a reconnect
      if (last && typeof w.end_s === 'number' && w.end_s <= last.end_s) {
        return w.id && last.id === w.id ? { ...state, window: w, windows: [...state.windows.slice(0, -1), w] } : state;
      }
      const windows = [...state.windows, w];
      if (windows.length > MAX_WINDOWS) windows.splice(0, windows.length - MAX_WINDOWS);
      return { ...state, window: w, windows };
    }
    case 'alert': {
      const a = (msg as { alert?: Alert }).alert;
      return a && a.id ? upsertAlert(state, a, true) : state;
    }
    case 'quality': {
      const m = msg as QualityMsg;
      return { ...state, quality: { state: m.state ?? 'ok', detail: m.detail ?? '', sqi: typeof m.sqi === 'number' ? m.sqi : null } };
    }
    case 'status': {
      const m = msg as StatusMsg;
      const stream = m.stream === 'connected' || m.stream === 'disconnected' ? m.stream : state.stream;
      // Keep demo Stop/Restart in sync: a disconnected status means nothing is streaming.
      const session = m.stream === 'disconnected' && state.session?.active
        ? { ...state.session, active: false }
        : state.session;
      return {
        ...state,
        stream,
        session,
        lastSampleTime: m.last_sample_time ?? state.lastSampleTime,
      };
    }
    case 'signal':
    case 'beat':
      return state; // handled outside React
    default:
      return { ...state, unknownMessages: state.unknownMessages + 1 };
  }
}

/** Parse one WebSocket text frame. Returns null for anything that is not a JSON object with a string `type`. */
export function parseLiveMessage(data: unknown): (LiveMsg | { type: string }) | null {
  if (typeof data !== 'string') return null;
  try {
    const o: unknown = JSON.parse(data);
    if (o && typeof o === 'object' && typeof (o as { type?: unknown }).type === 'string') return o as { type: string };
  } catch { /* not JSON */ }
  return null;
}

/** Clinical alerts are suppressed while the signal cannot be trusted (BRD §11). */
export function alertsSuppressed(q: QualityState): boolean {
  return q === 'lead_off' || q === 'low_sqi' || q === 'gap';
}

/** Backoff schedule for reconnects: 0.5 s doubling to 10 s, with ±20 % jitter. */
export function backoffMs(attempt: number, rand: () => number = Math.random): number {
  const base = Math.min(10_000, 500 * 2 ** Math.max(0, attempt - 1));
  return Math.round(base * (0.8 + 0.4 * rand()));
}
