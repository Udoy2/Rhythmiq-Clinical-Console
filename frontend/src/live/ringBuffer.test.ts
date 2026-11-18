import { describe, expect, it } from 'vitest';
import { SampleRing } from './ringBuffer';

describe('SampleRing', () => {
  it('appends and reads by absolute index', () => {
    const r = new SampleRing(10, 1); // capacity 10
    expect(r.isEmpty).toBe(true);
    r.write(0, [1, 2, 3]);
    r.write(3, [4, 5]);
    expect(r.start).toBe(0);
    expect(r.end).toBe(5);
    expect(Array.from(r.slice(0, 5))).toEqual([1, 2, 3, 4, 5]);
    expect(r.endTime).toBeCloseTo(0.5);
  });

  it('starts at the first t0 it sees (joining a session mid-way)', () => {
    const r = new SampleRing(10, 1);
    r.write(6120, [7, 8]);
    expect(r.start).toBe(6120);
    expect(r.end).toBe(6122);
    expect(r.get(6121)).toBe(8);
    expect(Number.isNaN(r.get(6119))).toBe(true);
    expect(Number.isNaN(r.get(6122))).toBe(true);
  });

  it('wraps and drops the oldest samples', () => {
    const r = new SampleRing(10, 1);
    r.write(0, Array.from({ length: 8 }, (_, i) => i));
    r.write(8, Array.from({ length: 8 }, (_, i) => 8 + i));
    expect(r.end).toBe(16);
    expect(r.start).toBe(6);
    expect(r.length).toBe(10);
    expect(Array.from(r.slice(6, 16))).toEqual([6, 7, 8, 9, 10, 11, 12, 13, 14, 15]);
    expect(Number.isNaN(r.get(5))).toBe(true);
  });

  it('marks a jump in t0 as a gap of NaN, never interpolating', () => {
    const r = new SampleRing(10, 1);
    r.write(0, [1, 1]);
    const gap = r.write(5, [2, 2]);
    expect(gap).toBe(3);
    const s = Array.from(r.slice(0, 7));
    expect(s.slice(0, 2)).toEqual([1, 1]);
    expect(s.slice(2, 5).every(Number.isNaN)).toBe(true);
    expect(s.slice(5)).toEqual([2, 2]);
  });

  it('restarts when the gap is longer than the ring', () => {
    const r = new SampleRing(10, 1);
    r.write(0, [1, 2, 3]);
    r.write(100, [9]);
    expect(r.start).toBe(100);
    expect(r.end).toBe(101);
    expect(r.get(100)).toBe(9);
    expect(Number.isNaN(r.get(2))).toBe(true);
  });

  it('overwrites overlapping re-delivery in place and ignores data before the start', () => {
    const r = new SampleRing(10, 1);
    r.write(10, [1, 2, 3, 4]);
    r.write(12, [30, 40, 50]);
    expect(Array.from(r.slice(10, 15))).toEqual([1, 2, 30, 40, 50]);
    r.write(8, [7, 7, 9]);
    expect(r.start).toBe(10);
    expect(r.get(10)).toBe(9);
    expect(r.end).toBe(15);
  });

  it('handles a block larger than the capacity', () => {
    const r = new SampleRing(10, 1);
    r.write(0, Array.from({ length: 25 }, (_, i) => i));
    expect(r.start).toBe(15);
    expect(r.end).toBe(25);
    expect(Array.from(r.slice(15, 25))).toEqual([15, 16, 17, 18, 19, 20, 21, 22, 23, 24]);
  });

  it('stores null as NaN, finds maxima and resets with a new rate', () => {
    const r = new SampleRing(10, 1);
    r.write(0, [0.1, null, 0.9, 0.2]);
    expect(Number.isNaN(r.get(1))).toBe(true);
    expect(r.maxIn(0, 3)).toBeCloseTo(0.9);
    expect(Number.isNaN(r.maxIn(1, 1))).toBe(true);
    r.reset(20);
    expect(r.isEmpty).toBe(true);
    expect(r.capacity).toBe(20);
    expect(r.fs).toBe(20);
  });

  it('keeps at least two minutes at 360 Hz', () => {
    const r = new SampleRing(360, 180);
    const block = new Float32Array(36).fill(1);
    for (let t = 0; t < 360 * 200; t += 36) r.write(t, block);
    expect(r.endTime - r.startTime).toBeGreaterThanOrEqual(120);
  });
});
