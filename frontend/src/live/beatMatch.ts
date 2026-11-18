import type { Beat, BeatLabel } from '../api/types';

/** A beat with its time in seconds since session start (sample / device fs). */
export interface TimedBeat extends Beat { t: number }

/** First index i with beats[i].t >= t (beats sorted by t). */
export function lowerBound(beats: readonly { t: number }[], t: number): number {
  let lo = 0, hi = beats.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (beats[mid].t < t) lo = mid + 1; else hi = mid;
  }
  return lo;
}

/** Index of the beat nearest to time `t` within `tolS`, or -1. */
export function nearestBeat(beats: readonly { t: number }[], t: number, tolS: number): number {
  const i = lowerBound(beats, t);
  let best = -1, bestD = tolS;
  for (const j of [i - 1, i]) {
    if (j < 0 || j >= beats.length) continue;
    const d = Math.abs(beats[j].t - t);
    if (d <= bestD) { best = j; bestD = d; }
  }
  return best;
}

/** [from, to) index range of beats with fromT <= t <= toT. */
export function visibleRange(beats: readonly { t: number }[], fromT: number, toT: number): [number, number] {
  const a = lowerBound(beats, fromT);
  let b = lowerBound(beats, toT);
  while (b < beats.length && beats[b].t <= toT) b++;
  return [a, b];
}

export type Agreement = 'match' | 'mismatch' | 'none';
/** Model label vs. cardiologist (replay) label. `none` when there is no ground truth for the beat. */
export function agreement(b: { label: BeatLabel; truth: BeatLabel | null }): Agreement {
  if (b.truth === null || b.truth === undefined) return 'none';
  return b.truth === b.label ? 'match' : 'mismatch';
}

export interface AgreementStats { withTruth: number; match: number; mismatch: number; unknown: number; pct: number | null }
export function agreementStats(beats: readonly { label: BeatLabel; truth: BeatLabel | null }[]): AgreementStats {
  let withTruth = 0, match = 0, unknown = 0;
  for (const b of beats) {
    if (b.truth === null || b.truth === undefined) continue;
    withTruth++;
    if (b.label === 'Unknown') unknown++;
    if (b.label === b.truth) match++;
  }
  return { withTruth, match, mismatch: withTruth - match, unknown, pct: withTruth ? (100 * match) / withTruth : null };
}

/**
 * Time-ordered beat list with de-duplication (same sample re-delivered after a reconnect replaces the old
 * entry) and pruning of beats older than the retained history.
 */
export class BeatStore {
  readonly items: TimedBeat[] = [];
  /** Bumped on every change, so renderers can cheaply detect updates. */
  version = 0;

  clear(): void {
    this.items.length = 0;
    this.version++;
  }

  add(b: TimedBeat): void {
    const a = this.items;
    const n = a.length;
    if (n === 0 || b.t > a[n - 1].t) {
      a.push(b);
    } else {
      const i = lowerBound(a, b.t);
      if (i < n && a[i].sample === b.sample) a[i] = b;
      else a.splice(i, 0, b);
    }
    this.version++;
  }

  pruneBefore(t: number): void {
    const i = lowerBound(this.items, t);
    if (i > 0) {
      this.items.splice(0, i);
      this.version++;
    }
  }
}
