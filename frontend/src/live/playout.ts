/**
 * Playout clocks: map wall-clock time to "stream time" (seconds since session start) for the renderer.
 */
export interface Clock {
  /** Stream time (s) to show at the right edge of the sweep at wall time `nowMs`. */
  now(nowMs: number): number;
}

/**
 * Jitter-buffered live clock. Data arrives in ~100 ms bursts with network jitter; the playhead trails the
 * newest sample by `delayS` of wall time and advances smoothly at the measured stream rate (which is the
 * replay speed, 1.0 for a real device), with a gentle proportional correction. It never runs past the data
 * and never moves backwards; if it falls far behind (tab was hidden, reconnect) it snaps.
 */
export class PlayoutClock implements Clock {
  private head = 0;
  private play = 0;
  private lastNow = 0;
  private started = false;
  private rate = 1;
  private arrivals: { t: number; h: number }[] = [];

  constructor(public delayS = 0.22, private snapS = 1.5, private gain = 1.5) {}

  reset(): void {
    this.started = false;
    this.arrivals = [];
    this.rate = 1;
    this.head = 0;
    this.play = 0;
  }

  /** Newest stream time (s) now available. */
  onData(headS: number, nowMs: number): void {
    if (headS < this.head - 1) {
      // time went backwards: a new stream
      this.reset();
    }
    this.head = Math.max(this.head, headS);
    const a = this.arrivals;
    a.push({ t: nowMs, h: headS });
    while (a.length > 2 && nowMs - a[0].t > 3000) a.shift();
    if (a.length >= 4) {
      const dt = (a[a.length - 1].t - a[0].t) / 1000;
      const dh = a[a.length - 1].h - a[0].h;
      if (dt > 0.25 && dh > 0) this.rate = Math.min(64, Math.max(0.05, dh / dt));
    }
  }

  get headS(): number { return this.head; }
  get streamRate(): number { return this.rate; }

  now(nowMs: number): number {
    const target = Math.max(0, this.head - this.delayS * this.rate);
    if (!this.started) {
      this.started = true;
      this.play = target;
      this.lastNow = nowMs;
      return this.play;
    }
    const dt = Math.max(0, (nowMs - this.lastNow) / 1000);
    this.lastNow = nowMs;
    const err = target - this.play;
    if (Math.abs(err) > this.snapS * Math.max(1, this.rate)) {
      this.play = target; // far off (reconnect, new stream, tab was hidden): snap
      return this.play;
    }
    if (dt > 1) {
      this.play = Math.max(this.play, target);
      return this.play;
    }
    const speed = Math.max(0, this.rate + err * this.gain);
    this.play = Math.min(this.head, this.play + speed * dt);
    return this.play;
  }
}

/** Deterministic clock for stored-session replay: position = anchor + elapsed × speed, clamped to the data. */
export class ReplayClock implements Clock {
  private anchorS: number;
  private anchorMs: number | null = null;
  private _speed = 1;
  private _playing = false;

  constructor(private startS: number, private endS: number) {
    this.anchorS = startS;
  }

  setBounds(startS: number, endS: number): void {
    this.startS = startS;
    this.endS = endS;
    this.anchorS = Math.min(endS, Math.max(startS, this.anchorS));
  }
  get playing(): boolean { return this._playing; }
  get speed(): number { return this._speed; }
  get start(): number { return this.startS; }
  get end(): number { return this.endS; }

  now(nowMs: number): number {
    if (!this._playing || this.anchorMs === null) return this.anchorS;
    const t = this.anchorS + ((nowMs - this.anchorMs) / 1000) * this._speed;
    if (t >= this.endS) {
      this.anchorS = this.endS;
      this._playing = false;
      this.anchorMs = null;
      return this.endS;
    }
    return t;
  }
  play(nowMs: number): void {
    if (this.anchorS >= this.endS) this.anchorS = this.startS;
    this.anchorMs = nowMs;
    this._playing = true;
  }
  pause(nowMs: number): void {
    this.anchorS = this.now(nowMs);
    this._playing = false;
    this.anchorMs = null;
  }
  seek(tS: number, nowMs: number): void {
    this.anchorS = Math.min(this.endS, Math.max(this.startS, tS));
    if (this._playing) this.anchorMs = nowMs;
  }
  setSpeed(speed: number, nowMs: number): void {
    this.anchorS = this.now(nowMs);
    if (this._playing) this.anchorMs = nowMs;
    this._speed = speed;
  }
}
