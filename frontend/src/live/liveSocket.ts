import { freshAccessToken, refreshTokens } from '../api/client';
import { transport, wsUrl, type SocketLike } from '../api/transport';
import type { LiveMsg } from '../api/types';
import { backoffMs, parseLiveMessage, type ConnState } from './liveReducer';

/** Application close codes used by the backend (`api/ws.py`). */
export const CLOSE_UNAUTHENTICATED = 4401;
export const CLOSE_FORBIDDEN = 4403;

export interface LiveSocketHandlers {
  onMessage(msg: LiveMsg | { type: string }): void;
  onConn(conn: ConnState, info?: { attempt: number; retryAt: number | null; denied?: string | null }): void;
}

/**
 * `WS /v1/live/{patient_id}?token=` with automatic reconnect (exponential backoff + jitter). A fresh access
 * token is obtained before every attempt, so an expired token does not strand the console.
 */
export class LiveSocket {
  private ws: SocketLike | null = null;
  private stopped = true;
  private attempt = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private generation = 0;

  constructor(private patientId: string, private h: LiveSocketHandlers) {}

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.attempt = 0;
    if (typeof window !== 'undefined') window.addEventListener('online', this.onOnline);
    void this.open();
  }

  stop(): void {
    this.stopped = true;
    this.generation++;
    if (typeof window !== 'undefined') window.removeEventListener('online', this.onOnline);
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
      try { ws.close(1000, 'client closed'); } catch { /* already closed */ }
    }
    this.h.onConn('closed');
  }

  private onOnline = () => {
    if (this.stopped || this.ws) return;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    void this.open();
  };

  private async open(): Promise<void> {
    const gen = ++this.generation;
    this.h.onConn(this.attempt === 0 ? 'connecting' : 'reconnecting', { attempt: this.attempt, retryAt: null });
    const token = await freshAccessToken();
    if (this.stopped || gen !== this.generation) return;
    if (!token) {
      // Signed out (refresh rejected) or the auth endpoint is unreachable: retry later, the app shell handles sign-out.
      this.scheduleRetry();
      return;
    }
    let ws: SocketLike;
    try {
      ws = transport.socket(wsUrl(`/live/${encodeURIComponent(this.patientId)}`, { token }));
    } catch {
      this.scheduleRetry();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      if (gen !== this.generation) return;
      this.attempt = 0;
      this.h.onConn('open');
    };
    ws.onmessage = (ev) => {
      if (gen !== this.generation) return;
      const msg = parseLiveMessage(ev.data);
      if (msg) this.h.onMessage(msg);
    };
    ws.onerror = () => { /* a close event always follows */ };
    ws.onclose = (ev) => {
      if (gen !== this.generation) return;
      this.ws = null;
      if (this.stopped) return;
      if (ev?.code === CLOSE_FORBIDDEN) {
        // Not permitted for this patient: retrying cannot help.
        this.stopped = true;
        this.h.onConn('closed', { attempt: this.attempt, retryAt: null, denied: ev.reason || 'Not permitted for this patient.' });
        return;
      }
      if (ev?.code === CLOSE_UNAUTHENTICATED) {
        // The token was rejected: rotate it before the next attempt (signs out if the refresh is rejected too).
        void refreshTokens().finally(() => { if (!this.stopped && gen === this.generation) this.scheduleRetry(); });
        return;
      }
      this.scheduleRetry();
    };
  }

  private scheduleRetry(): void {
    if (this.stopped) return;
    this.attempt++;
    const delay = backoffMs(this.attempt);
    this.h.onConn('reconnecting', { attempt: this.attempt, retryAt: Date.now() + delay });
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.open();
    }, delay);
  }
}
