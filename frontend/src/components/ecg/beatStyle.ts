import type { BeatLabel, Tier } from '../../api/types';

export type Shape = 'circle' | 'triangle' | 'square' | 'diamond' | 'ring' | 'octagon';
export type Pattern = 'solid' | 'diagonal' | 'cross' | 'dots' | 'hollow';

export interface BeatStyle {
  letter: string;
  name: string;
  /** Fill colour for shapes / blocks (>= 3:1 on the dark surface). */
  color: string;
  /** Text colour (>= 4.5:1 on the dark surface). */
  text: string;
  shape: Shape;
  pattern: Pattern;
  /** Ribbon block height as a fraction of the ribbon. */
  height: number;
}

/** Class encoding is always colour + letter + shape + pattern (never colour alone). */
export const BEAT_STYLE: Record<BeatLabel, BeatStyle> = {
  N: { letter: 'N', name: 'Normal', color: '#00A896', text: '#2CC7B5', shape: 'circle', pattern: 'solid', height: 0.5 },
  S: { letter: 'S', name: 'Supraventricular ectopic', color: '#C9820A', text: '#EBA63A', shape: 'triangle', pattern: 'diagonal', height: 0.75 },
  V: { letter: 'V', name: 'Ventricular ectopic', color: '#E0484D', text: '#FF8589', shape: 'square', pattern: 'cross', height: 1 },
  F: { letter: 'F', name: 'Fusion', color: '#8E7CF0', text: '#B9ADFF', shape: 'diamond', pattern: 'dots', height: 0.75 },
  Unknown: { letter: '?', name: 'Unknown (low confidence)', color: '#8A9BAC', text: '#A9B8C6', shape: 'ring', pattern: 'hollow', height: 0.5 },
};

export interface TierStyle { name: string; meaning: string; color: string; text: string; bg: string; shape: Shape; action: string }
export const TIER_STYLE: Record<Tier, TierStyle> = {
  green: { name: 'Green', meaning: 'Normal', color: '#00A896', text: '#2CC7B5', bg: '#0A3B3A', shape: 'circle', action: 'No action' },
  amber: { name: 'Amber', meaning: 'Watch', color: '#C9820A', text: '#EBA63A', bg: '#3D2C0F', shape: 'triangle', action: 'Review within 24 h' },
  orange: { name: 'Orange', meaning: 'Urgent', color: '#E8762B', text: '#FF9A55', bg: '#44260F', shape: 'diamond', action: 'Acknowledge within 30 min' },
  red: { name: 'Red', meaning: 'Critical', color: '#E0484D', text: '#FF8589', bg: '#471A20', shape: 'octagon', action: 'Immediate review' },
};
export const tierLabel = (t: Tier) => `${TIER_STYLE[t].name} — ${TIER_STYLE[t].meaning}`;

/** Risk score bands used by the gauge when drawing the tier scale (the tier itself always comes from the API). */
export const TIER_BANDS: { tier: Tier; from: number; to: number }[] = [
  { tier: 'green', from: 0, to: 25 },
  { tier: 'amber', from: 25, to: 50 },
  { tier: 'orange', from: 50, to: 80 },
  { tier: 'red', from: 80, to: 100 },
];

export function shapePath(ctx: CanvasRenderingContext2D | Path2D, shape: Shape, x: number, y: number, r: number): void {
  switch (shape) {
    case 'circle':
    case 'ring':
      ctx.moveTo(x + r, y);
      ctx.arc(x, y, r, 0, Math.PI * 2);
      break;
    case 'triangle':
      ctx.moveTo(x, y - r * 1.15);
      ctx.lineTo(x + r * 1.1, y + r * 0.85);
      ctx.lineTo(x - r * 1.1, y + r * 0.85);
      ctx.closePath();
      break;
    case 'square':
      ctx.rect(x - r * 0.9, y - r * 0.9, r * 1.8, r * 1.8);
      break;
    case 'diamond':
      ctx.moveTo(x, y - r * 1.25);
      ctx.lineTo(x + r * 1.1, y);
      ctx.lineTo(x, y + r * 1.25);
      ctx.lineTo(x - r * 1.1, y);
      ctx.closePath();
      break;
    case 'octagon': {
      const k = r * 0.45;
      ctx.moveTo(x - k, y - r); ctx.lineTo(x + k, y - r); ctx.lineTo(x + r, y - k); ctx.lineTo(x + r, y + k);
      ctx.lineTo(x + k, y + r); ctx.lineTo(x - k, y + r); ctx.lineTo(x - r, y + k); ctx.lineTo(x - r, y - k);
      ctx.closePath();
      break;
    }
  }
}

/** Draw a beat-class marker. Unknown is a hollow ring; the others are filled. */
export function drawBeatShape(ctx: CanvasRenderingContext2D, label: BeatLabel, x: number, y: number, r: number): void {
  const st = BEAT_STYLE[label];
  ctx.beginPath();
  shapePath(ctx, st.shape, x, y, r);
  if (st.shape === 'ring') {
    ctx.strokeStyle = st.color;
    ctx.lineWidth = 1.5;
    ctx.stroke();
  } else {
    ctx.fillStyle = st.color;
    ctx.fill();
  }
}

/** SVG path for the same shapes (legend, badges), centred in a 16×16 box. */
export function shapeSvgPath(shape: Shape): string {
  switch (shape) {
    case 'circle':
    case 'ring': return 'M8 2.5a5.5 5.5 0 1 0 0 11a5.5 5.5 0 1 0 0-11z';
    case 'triangle': return 'M8 2 L14.2 13 L1.8 13 Z';
    case 'square': return 'M3 3 H13 V13 H3 Z';
    case 'diamond': return 'M8 1.2 L14 8 L8 14.8 L2 8 Z';
    case 'octagon': return 'M5.3 2 H10.7 L14 5.3 V10.7 L10.7 14 H5.3 L2 10.7 V5.3 Z';
  }
}

const patternCache = new Map<string, CanvasPattern | string>();
/** Fill style for a ribbon block of the given class (colour + pattern). */
export function beatFill(ctx: CanvasRenderingContext2D, label: BeatLabel, dpr = 1): CanvasPattern | string {
  const st = BEAT_STYLE[label];
  if (st.pattern === 'solid') return st.color;
  const key = `${label}@${dpr}`;
  const hit = patternCache.get(key);
  if (hit) return hit;
  const n = 6;
  const c = document.createElement('canvas');
  c.width = c.height = n * dpr;
  const g = c.getContext('2d');
  if (!g) return st.color;
  g.scale(dpr, dpr);
  if (st.pattern === 'hollow') {
    g.clearRect(0, 0, n, n);
  } else {
    g.fillStyle = st.color;
    g.globalAlpha = st.pattern === 'cross' ? 1 : 0.28;
    g.fillRect(0, 0, n, n);
    g.globalAlpha = 1;
  }
  g.lineWidth = 1.2;
  if (st.pattern === 'diagonal') {
    g.strokeStyle = st.color;
    g.beginPath();
    for (const o of [-n, 0, n]) { g.moveTo(o, n); g.lineTo(o + n, 0); }
    g.stroke();
  } else if (st.pattern === 'cross') {
    // dark horizontal bars cut into the solid block
    g.strokeStyle = 'rgba(11,27,43,0.55)';
    g.beginPath();
    g.moveTo(0, 2.5); g.lineTo(n, 2.5);
    g.stroke();
  } else if (st.pattern === 'dots') {
    g.fillStyle = st.color;
    g.beginPath(); g.arc(1.5, 1.5, 1.1, 0, Math.PI * 2); g.arc(4.5, 4.5, 1.1, 0, Math.PI * 2); g.fill();
  }
  const p = ctx.createPattern(c, 'repeat');
  if (!p) return st.color;
  if (dpr !== 1 && typeof DOMMatrix !== 'undefined') p.setTransform(new DOMMatrix().scale(1 / dpr));
  patternCache.set(key, p);
  return p;
}

/** CSS background for the same patterns (legend swatches, DOM blocks). */
export function beatCssBackground(label: BeatLabel): string {
  const st = BEAT_STYLE[label];
  switch (st.pattern) {
    case 'solid': return st.color;
    case 'diagonal': return `repeating-linear-gradient(135deg, ${st.color} 0 1.5px, ${st.color}47 1.5px 5px)`;
    case 'cross': return `repeating-linear-gradient(0deg, ${st.color} 0 4px, rgba(11,27,43,0.55) 4px 5.5px)`;
    case 'dots': return `radial-gradient(${st.color} 1.2px, ${st.color}47 1.4px) 0 0 / 5px 5px`;
    case 'hollow': return 'transparent';
  }
}
