import { describe, expect, it } from 'vitest';
import type { BeatLabel } from '../api/types';
import { agreement, agreementStats, BeatStore, lowerBound, nearestBeat, visibleRange, type TimedBeat } from './beatMatch';

const mk = (sample: number, label: BeatLabel = 'N', truth: BeatLabel | null = null): TimedBeat => ({
  sample, time_s: sample / 360, t: sample / 360, label, confidence: 0.9,
  probs: { N: 0.9, S: 0.03, V: 0.05, F: 0.02 }, model_version: 'm', truth,
});

describe('beat lookup', () => {
  const beats = [1, 2, 3.5, 5].map((t) => ({ t }));

  it('lowerBound finds the first beat at or after t', () => {
    expect(lowerBound(beats, 0)).toBe(0);
    expect(lowerBound(beats, 2)).toBe(1);
    expect(lowerBound(beats, 2.1)).toBe(2);
    expect(lowerBound(beats, 9)).toBe(4);
    expect(lowerBound([], 1)).toBe(0);
  });

  it('nearestBeat respects the tolerance', () => {
    expect(nearestBeat(beats, 3.4, 0.2)).toBe(2);
    expect(nearestBeat(beats, 2.6, 0.2)).toBe(-1);
    expect(nearestBeat(beats, 2.6, 1)).toBe(1);
    expect(nearestBeat(beats, 100, 0.5)).toBe(-1);
    expect(nearestBeat(beats, 0.9, 0.2)).toBe(0);
  });

  it('visibleRange is inclusive of both ends', () => {
    expect(visibleRange(beats, 2, 5)).toEqual([1, 4]);
    expect(visibleRange(beats, 2.5, 3)).toEqual([2, 2]);
    expect(visibleRange(beats, -1, 100)).toEqual([0, 4]);
  });
});

describe('model vs. ground truth', () => {
  it('classifies agreement', () => {
    expect(agreement(mk(1, 'V', 'V'))).toBe('match');
    expect(agreement(mk(1, 'N', 'V'))).toBe('mismatch');
    expect(agreement(mk(1, 'Unknown', 'N'))).toBe('mismatch');
    expect(agreement(mk(1, 'N', null))).toBe('none');
  });

  it('computes agreement statistics over beats that have truth', () => {
    const s = agreementStats([mk(1, 'N', 'N'), mk(2, 'V', 'V'), mk(3, 'N', 'S'), mk(4, 'Unknown', 'N'), mk(5, 'N', null)]);
    expect(s).toMatchObject({ withTruth: 4, match: 2, mismatch: 2, unknown: 1 });
    expect(s.pct).toBeCloseTo(50);
    expect(agreementStats([mk(1)]).pct).toBeNull();
  });
});

describe('BeatStore', () => {
  it('keeps beats ordered, replaces duplicates and prunes', () => {
    const s = new BeatStore();
    s.add(mk(360));
    s.add(mk(1080));
    s.add(mk(720, 'V'));
    expect(s.items.map((b) => b.sample)).toEqual([360, 720, 1080]);
    s.add(mk(720, 'S')); // re-delivered after reconnect
    expect(s.items.length).toBe(3);
    expect(s.items[1].label).toBe('S');
    const v = s.version;
    s.pruneBefore(1.5);
    expect(s.items.map((b) => b.sample)).toEqual([720, 1080]);
    expect(s.version).toBeGreaterThan(v);
    s.clear();
    expect(s.items.length).toBe(0);
  });
});
