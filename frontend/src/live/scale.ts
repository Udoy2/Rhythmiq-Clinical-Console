/**
 * ECG paper geometry. The panel is calibrated so that the reference window (6 s at 25 mm/s = 150 mm) spans
 * the panel width exactly; everything else derives from px/mm.
 */
export const REF_SPEED_MM_S = 25;
export const REF_WINDOW_S = 6;
export const REF_WIDTH_MM = REF_SPEED_MM_S * REF_WINDOW_S; // 150 mm
export const SPEEDS_MM_S = [12.5, 25, 50] as const;
export const GAINS_MM_MV = [5, 10, 20] as const;
export type SpeedMmS = (typeof SPEEDS_MM_S)[number];
export type GainMmMv = (typeof GAINS_MM_MV)[number];

export interface EcgScale {
  widthPx: number;
  heightPx: number;
  pxPerMm: number;
  pxPerSec: number;
  pxPerMv: number;
  /** Seconds of signal visible across the panel width. */
  windowS: number;
  /** y (px) of the 0 mV isoelectric line. */
  baselinePx: number;
  speedMmS: number;
  gainMmMv: number;
}

/** px per mm of paper such that `refWindowS` seconds at 25 mm/s span `widthPx`. */
export function pxPerMmForWidth(widthPx: number, refWindowS: number = REF_WINDOW_S): number {
  return widthPx / (REF_SPEED_MM_S * refWindowS);
}

/**
 * @param widthPx     plot width in CSS px
 * @param heightMm    plot height in mm of paper (the px height follows from px/mm)
 * @param baselineFrac position of 0 mV from the top, as a fraction of the height
 * @param refWindowS  seconds that fit the width at 25 mm/s (6 for the live sweep, 10 for evidence strips)
 */
export function computeScale(
  widthPx: number, speedMmS: number = 25, gainMmMv: number = 10, heightMm = 40, baselineFrac = 0.6,
  refWindowS: number = REF_WINDOW_S,
): EcgScale {
  const pxPerMm = pxPerMmForWidth(widthPx, refWindowS);
  const heightPx = pxPerMm * heightMm;
  const pxPerSec = pxPerMm * speedMmS;
  return {
    widthPx,
    heightPx,
    pxPerMm,
    pxPerSec,
    pxPerMv: pxPerMm * gainMmMv,
    windowS: pxPerSec > 0 ? widthPx / pxPerSec : 0,
    baselinePx: heightPx * baselineFrac,
    speedMmS,
    gainMmMv,
  };
}

/** x (px) of time `t` when the right edge of the panel shows `headS`. */
export function xOfTime(s: EcgScale, t: number, headS: number): number {
  return s.widthPx - (headS - t) * s.pxPerSec;
}
export function timeOfX(s: EcgScale, x: number, headS: number): number {
  return headS - (s.widthPx - x) / s.pxPerSec;
}
export function yOfMv(s: EcgScale, mv: number): number {
  return s.baselinePx - mv * s.pxPerMv;
}

/**
 * Vertical grid lines. The grid is attached to the paper (time), so it scrolls with the trace: line k sits at
 * k mm of paper = k / speed seconds. Returns x positions and whether each is a major (5 mm) line.
 */
export function verticalGridLines(s: EcgScale, headS: number): { x: number; major: boolean; mm: number }[] {
  const out: { x: number; major: boolean; mm: number }[] = [];
  if (s.pxPerMm <= 0) return out;
  const leftMm = (headS - s.windowS) * s.speedMmS;
  const rightMm = headS * s.speedMmS;
  for (let mm = Math.ceil(leftMm - 1e-9); mm <= rightMm + 1e-9; mm++) {
    out.push({ x: (mm - leftMm) * s.pxPerMm, major: mm % 5 === 0, mm });
  }
  return out;
}

/** Horizontal grid lines, anchored on the baseline (0 mV). */
export function horizontalGridLines(s: EcgScale): { y: number; major: boolean; mm: number }[] {
  const out: { y: number; major: boolean; mm: number }[] = [];
  if (s.pxPerMm <= 0) return out;
  const up = Math.floor(s.baselinePx / s.pxPerMm + 1e-9);
  const down = Math.floor((s.heightPx - s.baselinePx) / s.pxPerMm + 1e-9);
  for (let mm = -down; mm <= up; mm++) out.push({ y: s.baselinePx - mm * s.pxPerMm, major: mm % 5 === 0, mm });
  return out;
}

export function fmtClock(t: number): string {
  const neg = t < 0;
  t = Math.abs(t);
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = Math.floor(t % 60);
  const core = h > 0 ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
  return neg ? `-${core}` : core;
}
