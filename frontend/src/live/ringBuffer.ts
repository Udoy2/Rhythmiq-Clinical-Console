/**
 * Fixed-capacity ring of display samples addressed by absolute sample index (index 0 = session start).
 * Missing samples (stream gaps) are stored as NaN and are never interpolated.
 */
export class SampleRing {
  private buf: Float32Array;
  /** Absolute index one past the newest sample written. */
  private head = 0;
  /** Absolute index of the oldest sample that may still be read (>= head - capacity). */
  private tail = 0;
  private empty = true;
  /** Bumped on every write/reset so renderers can detect in-place changes (e.g. a chunk loaded into a pre-filled ring). */
  version = 0;

  constructor(public fs: number, public readonly seconds: number) {
    this.buf = new Float32Array(Math.max(1, Math.ceil(fs * seconds)));
  }

  get capacity(): number { return this.buf.length; }
  /** First readable absolute index. */
  get start(): number { return this.tail; }
  /** One past the last written absolute index. */
  get end(): number { return this.head; }
  get length(): number { return this.head - this.tail; }
  get isEmpty(): boolean { return this.empty; }
  get startTime(): number { return this.tail / this.fs; }
  get endTime(): number { return this.head / this.fs; }

  reset(fs: number = this.fs): void {
    if (fs !== this.fs || this.buf.length !== Math.ceil(fs * this.seconds)) {
      this.fs = fs;
      this.buf = new Float32Array(Math.max(1, Math.ceil(fs * this.seconds)));
    }
    this.head = 0;
    this.tail = 0;
    this.empty = true;
    this.version++;
  }

  /**
   * Write `values` starting at absolute index `t0`.
   *  - t0 == end: plain append
   *  - t0 >  end: the hole is filled with NaN (a gap), then append
   *  - t0 <  end: overlapping part overwrites in place (idempotent re-delivery); anything before `start` is dropped
   * Returns the number of gap samples inserted.
   */
  write(t0: number, values: ArrayLike<number | null>): number {
    const n = values.length;
    if (n === 0) return 0;
    t0 = Math.round(t0);
    this.version++;
    const cap = this.buf.length;
    let gap = 0;
    if (this.empty) {
      this.head = t0;
      this.tail = t0;
      this.empty = false;
    } else if (t0 > this.head) {
      gap = t0 - this.head;
      if (gap >= cap) {
        // the whole ring would be NaN — restart it at t0
        this.head = t0;
        this.tail = t0;
      } else {
        for (let i = this.head; i < t0; i++) this.buf[i % cap] = NaN;
        this.head = t0;
      }
    }
    const newHead = Math.max(this.head, t0 + n);
    const minIdx = newHead - cap;
    for (let k = 0; k < n; k++) {
      const idx = t0 + k;
      if (idx < minIdx || idx < this.tail) continue; // older than the ring / before its start: dropped
      const v = values[k];
      this.buf[idx % cap] = v === null || v === undefined ? NaN : v;
    }
    this.head = newHead;
    if (this.tail < minIdx) this.tail = minIdx;
    return gap;
  }

  /** Sample at absolute index, NaN if outside the ring or in a gap. */
  get(i: number): number {
    if (i < this.tail || i >= this.head) return NaN;
    return this.buf[i % this.buf.length];
  }

  /** Copy [from, to) into a new array (NaN where unavailable). */
  slice(from: number, to: number): Float32Array {
    const out = new Float32Array(Math.max(0, to - from));
    for (let i = from; i < to; i++) out[i - from] = this.get(i);
    return out;
  }

  /** Max finite value in [from, to], or NaN. */
  maxIn(from: number, to: number): number {
    let m = NaN;
    for (let i = Math.max(from, this.tail); i <= to && i < this.head; i++) {
      const v = this.buf[i % this.buf.length];
      if (v === v && !(v <= m)) m = v;
    }
    return m;
  }
}
