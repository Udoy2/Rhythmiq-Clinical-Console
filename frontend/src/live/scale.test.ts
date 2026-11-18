import { describe, expect, it } from 'vitest';
import { computeScale, fmtClock, horizontalGridLines, timeOfX, verticalGridLines, xOfTime, yOfMv } from './scale';

describe('ECG scale', () => {
  it('fits 6 s at 25 mm/s into the panel width', () => {
    const s = computeScale(900, 25, 10);
    expect(s.pxPerMm).toBeCloseTo(6);
    expect(s.pxPerSec).toBeCloseTo(150);
    expect(s.windowS).toBeCloseTo(6);
    expect(s.pxPerMv).toBeCloseTo(60); // 10 mm/mV
    expect(s.heightPx).toBeCloseTo(240); // 40 mm of paper
  });

  it('keeps px/mm fixed when speed or gain change', () => {
    const a = computeScale(1200, 12.5, 5);
    const b = computeScale(1200, 50, 20);
    expect(a.pxPerMm).toBeCloseTo(8);
    expect(b.pxPerMm).toBeCloseTo(8);
    expect(a.windowS).toBeCloseTo(12);
    expect(b.windowS).toBeCloseTo(3);
    expect(a.pxPerMv).toBeCloseTo(40);
    expect(b.pxPerMv).toBeCloseTo(160);
  });

  it('1 mV is 10 mm and 0.2 s is 5 mm at standard settings', () => {
    const s = computeScale(750, 25, 10);
    expect(yOfMv(s, 0) - yOfMv(s, 1)).toBeCloseTo(10 * s.pxPerMm);
    expect(xOfTime(s, 10.2, 12) - xOfTime(s, 10, 12)).toBeCloseTo(5 * s.pxPerMm);
  });

  it('maps time <-> x with the head at the right edge', () => {
    const s = computeScale(900, 25, 10);
    expect(xOfTime(s, 20, 20)).toBeCloseTo(900);
    expect(xOfTime(s, 14, 20)).toBeCloseTo(0);
    expect(timeOfX(s, 450, 20)).toBeCloseTo(17);
    expect(timeOfX(s, xOfTime(s, 18.37, 20), 20)).toBeCloseTo(18.37);
  });

  it('vertical grid lines are 1 mm apart, major every 5 mm, and scroll with time', () => {
    const s = computeScale(900, 25, 10);
    const lines = verticalGridLines(s, 6);
    expect(lines.length).toBe(151);
    expect(lines[1].x - lines[0].x).toBeCloseTo(6);
    expect(lines.filter((l) => l.major).length).toBe(31);
    // half a millimetre later (0.02 s) every line has moved left by half a millimetre
    const later = verticalGridLines(s, 6.02);
    expect(later[0].x).toBeCloseTo(3);
    expect(later[0].mm).toBe(1);
  });

  it('horizontal grid lines are anchored on the baseline', () => {
    const s = computeScale(900, 25, 10);
    const lines = horizontalGridLines(s);
    const zero = lines.find((l) => l.mm === 0);
    expect(zero?.y).toBeCloseTo(s.baselinePx);
    expect(zero?.major).toBe(true);
    expect(lines.every((l) => l.y >= -1e-6 && l.y <= s.heightPx + 1e-6)).toBe(true);
    expect(lines.length).toBe(41);
  });

  it('formats clock times', () => {
    expect(fmtClock(0)).toBe('0:00');
    expect(fmtClock(75.9)).toBe('1:15');
    expect(fmtClock(3725)).toBe('1:02:05');
  });
});
