import { createStore, type StoreApi } from 'zustand/vanilla';
import { normBeat } from '../api/normalize';
import type { Alert, BeatMsg, LiveMsg, SessionMsg, SignalMsg } from '../api/types';
import { BeatStore } from './beatMatch';
import { LiveSocket } from './liveSocket';
import { initialLiveState, reduceLive, type LiveEvent, type LiveState } from './liveReducer';
import { PlayoutClock, type Clock } from './playout';
import { SampleRing } from './ringBuffer';

/** What the ECG renderers read from. Mutable buffers — never put these in React state. */
export interface EcgSource {
  ring: SampleRing;
  beats: BeatStore;
}

const nowMs = () => (typeof performance !== 'undefined' ? performance.now() : Date.now());
/** Alerts arriving this soon after the socket opens are the server's connect backlog, not new events. */
export const BACKLOG_MS = 1500;

/** Seconds of signal kept for pause / scroll-back (requirement: >= 2 min). */
export const HISTORY_S = 180;

/**
 * One patient's live stream: owns the socket, the sample ring, the beat list and the playout clock, and
 * exposes the low-rate state through a vanilla zustand store.
 */
export class LiveFeed implements EcgSource {
  readonly ring = new SampleRing(360, HISTORY_S);
  readonly beats = new BeatStore();
  readonly clock = new PlayoutClock();
  readonly store: StoreApi<LiveState> = createStore<LiveState>(() => initialLiveState);
  /** Wall-clock ms at which the last signal frame arrived. */
  lastSignalAt: number | null = null;
  /** Device sample rate of the current session (beat / window sample indices use it). */
  deviceFs = 360;
  private socket: LiveSocket;
  private sessionId: string | null = null;
  private frameLeadOff = false;
  private beatsSincePrune = 0;
  /** performance.now() of the last socket open; alerts in the connect backlog are not "new". */
  private openedAt = -Infinity;

  constructor(readonly patientId: string) {
    this.socket = new LiveSocket(patientId, {
      onMessage: (m) => this.handle(m),
      onConn: (conn, info) => {
        if (conn === 'open') this.openedAt = nowMs();
        if (conn !== 'open' && this.lastSignalAt !== null) {
          this.dispatch({ kind: 'sample-time', iso: new Date(this.lastSignalAt).toISOString() });
        }
        this.dispatch({ kind: 'conn', conn, attempt: info?.attempt, retryAt: info?.retryAt, denied: info?.denied });
      },
    });
  }

  start(): void { this.socket.start(); }
  stop(): void { this.socket.stop(); }
  dispatch(ev: LiveEvent): void { this.store.setState((s) => reduceLive(s, ev)); }
  upsertAlert(alert: Alert): void { this.dispatch({ kind: 'alert-updated', alert }); }
  get liveClock(): Clock { return this.clock; }

  /** Public for tests and the mock: feed one parsed message. */
  handle(msg: LiveMsg | { type: string }): void {
    switch (msg.type) {
      case 'signal':
        this.onSignal(msg as SignalMsg);
        return;
      case 'beat':
        this.onBeat(msg as BeatMsg);
        return;
      case 'session':
        this.onSession(msg as SessionMsg);
        break;
      case 'alert': {
        // On connect the server replays the patient's unresolved alerts; those must not raise toasts.
        const a = (msg as { alert?: Alert }).alert;
        if (a && a.id && nowMs() - this.openedAt < BACKLOG_MS) {
          this.dispatch({ kind: 'alert-updated', alert: a });
          return;
        }
        break;
      }
    }
    this.dispatch({ kind: 'msg', msg });
  }

  private onSession(m: SessionMsg): void {
    if (typeof m.fs === 'number' && m.fs > 0) this.deviceFs = m.fs;
    if (m.session_id !== this.sessionId) {
      this.sessionId = m.session_id;
      this.ring.reset(this.ring.fs);
      this.beats.clear();
      this.clock.reset();
      this.frameLeadOff = false;
    }
  }

  private onSignal(m: SignalMsg): void {
    if (!Array.isArray(m.mv) || m.mv.length === 0 || typeof m.t0 !== 'number') return;
    const fs = typeof m.fs === 'number' && m.fs > 0 ? m.fs : this.deviceFs;
    if (this.ring.fs !== fs) this.ring.reset(fs);
    // t0 is in the device sample rate; the display signal may be at another rate.
    const t0 = fs === this.deviceFs ? m.t0 : Math.round((m.t0 * fs) / this.deviceFs);
    if (!this.ring.isEmpty && t0 + m.mv.length < this.ring.end - this.ring.capacity / 2) {
      // far in the past: the stream restarted without a session message
      this.ring.reset(fs);
      this.beats.clear();
      this.clock.reset();
    }
    this.ring.write(t0, m.mv);
    this.clock.onData(this.ring.endTime, nowMs());
    this.lastSignalAt = Date.now();
    // Fallback if the server only flags lead-off on frames and sends no `quality` message.
    const lo = m.lead_off === true;
    if (lo !== this.frameLeadOff) {
      this.frameLeadOff = lo;
      const q = this.store.getState().quality;
      if (lo && q.state === 'ok') {
        this.dispatch({ kind: 'msg', msg: { type: 'quality', state: 'lead_off', detail: 'Electrode contact lost' } });
      } else if (!lo && q.state === 'lead_off' && q.detail === 'Electrode contact lost') {
        this.dispatch({ kind: 'msg', msg: { type: 'quality', state: 'ok', detail: '' } });
      }
    }
  }

  private onBeat(m: BeatMsg): void {
    const b = normBeat(m as unknown as Record<string, unknown>, this.deviceFs);
    const t = typeof m.sample === 'number' ? m.sample / this.deviceFs : b.time_s;
    this.beats.add({ ...b, t });
    if (++this.beatsSincePrune >= 50) {
      this.beatsSincePrune = 0;
      this.beats.pruneBefore(this.ring.endTime - HISTORY_S - 5);
    }
  }
}
