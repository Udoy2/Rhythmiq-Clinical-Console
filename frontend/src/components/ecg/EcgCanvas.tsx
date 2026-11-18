import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import type { BeatLabel } from '../../api/types';
import { agreement, nearestBeat, visibleRange, type TimedBeat } from '../../live/beatMatch';
import type { EcgSource } from '../../live/liveFeed';
import { computeScale, fmtClock, horizontalGridLines, timeOfX, verticalGridLines, xOfTime, yOfMv, type EcgScale } from '../../live/scale';
import { BEAT_STYLE, drawBeatShape } from './beatStyle';
import { BeatTooltip } from './BeatTooltip';

export interface EcgCanvasProps {
  source: EcgSource;
  /** Stream time (s) shown at the right edge, for wall time `nowMs` (performance.now()). */
  getHead: (nowMs: number) => number;
  speedMmS: number;
  gainMmMv: number;
  /** Draw the cardiologist (replay ground-truth) row under the model row and box disagreements. */
  showTruth: boolean;
  /** Drag / arrow keys / horizontal wheel. Positive = back in time (seconds). */
  onPan?: (deltaS: number) => void;
  onTogglePause?: () => void;
  /** Seconds that fit the width at 25 mm/s: 6 for the live sweep (BRD), more for static strips. */
  refWindowS?: number;
  /** mm of paper shown vertically. Default: 40 mm, reduced in 5 mm steps (min 25) so the plot stays under ~300 px. */
  heightMm?: number;
  ariaLabel?: string;
  /** Reports the visible time range, for the ribbon viewport bracket. */
  viewRef?: { current: { from: number; to: number } | null };
}

/** Paper height for a panel width: 40 mm, stepping down to 25 mm so wide panels do not get taller than ~300 px. */
export function autoHeightMm(widthPx: number, refWindowS = 6): number {
  const pxPerMm = widthPx / (25 * refWindowS);
  return Math.max(25, Math.min(40, Math.floor(300 / pxPerMm / 5) * 5));
}

const BG = '#06131F';
const TRACE = '#D9F3EE';
const MINOR = 'rgba(126,164,196,0.11)';
const MAJOR = 'rgba(126,164,196,0.27)';
const FAINT = '#7F93A6';
const MUTED = '#A9B8C6';
const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
const ROW_H = 20;
const TOOLTIP_W = 232;

export function EcgCanvas({
  source, getHead, speedMmS, gainMmMv, showTruth, onPan, onTogglePause, refWindowS = 6, heightMm, ariaLabel, viewRef,
}: EcgCanvasProps) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [active, setActive] = useState<TimedBeat | null>(null);

  // Everything the draw loop reads lives in refs so the loop never restarts and React never re-renders per frame.
  const props = useRef({ source, getHead, speedMmS, gainMmMv, showTruth, refWindowS, heightMm });
  props.current = { source, getHead, speedMmS, gainMmMv, showTruth, refWindowS, heightMm };
  const hover = useRef<number | null>(null); // beat sample
  const pinned = useRef<number | null>(null);
  const activeRef = useRef<TimedBeat | null>(null);
  const scaleRef = useRef<EcgScale | null>(null);
  const headRef = useRef(0);
  const drag = useRef<{ x: number; moved: boolean; id: number } | null>(null);

  const bandH = showTruth ? ROW_H * 2 + 6 : 0;
  const scale = computeScale(Math.max(1, width), speedMmS, gainMmMv, heightMm ?? autoHeightMm(Math.max(1, width), refWindowS), 0.6, refWindowS);
  const cssH = Math.round(scale.heightPx + bandH);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const w = Math.floor(entries[0].contentRect.width);
      setWidth((prev) => (prev === w ? prev : w));
    });
    ro.observe(el);
    setWidth(Math.floor(el.clientWidth));
    return () => ro.disconnect();
  }, []);

  const setActiveBeat = useCallback((b: TimedBeat | null) => {
    if (activeRef.current?.sample === b?.sample && activeRef.current?.label === b?.label) return;
    activeRef.current = b;
    setActive(b);
  }, []);

  // ───────────── render loop ─────────────
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || width <= 0) return;
    const ctx = canvas.getContext('2d', { alpha: false });
    if (!ctx) return;
    let raf = 0;
    let last = { head: NaN, end: -1, ver: -1, key: '' };

    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      const p = props.current;
      const dpr = Math.min(3, window.devicePixelRatio || 1);
      const band = p.showTruth ? ROW_H * 2 + 6 : 0;
      const s = computeScale(width, p.speedMmS, p.gainMmMv, p.heightMm ?? autoHeightMm(width, p.refWindowS), 0.6, p.refWindowS);
      const H = Math.round(s.heightPx + band);
      const pw = Math.round(width * dpr), ph = Math.round(H * dpr);
      let resized = false;
      if (canvas.width !== pw || canvas.height !== ph) {
        canvas.width = pw;
        canvas.height = ph;
        resized = true;
      }
      const head = p.getHead(now);
      const ring = p.source.ring;
      const beats = p.source.beats;
      const key = `${p.speedMmS}|${p.gainMmMv}|${p.showTruth}|${hover.current}|${pinned.current}|${dpr}`;
      // Skip the frame when nothing visible changed (paused, stream stopped).
      if (!resized && head === last.head && ring.version === last.end && beats.version === last.ver && key === last.key) return;
      last = { head, end: ring.version, ver: beats.version, key };
      scaleRef.current = s;
      headRef.current = head;
      if (viewRef) viewRef.current = { from: head - s.windowS, to: head };

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      drawFrame(ctx, s, H, band, head, p.source, p.showTruth, hover.current, pinned.current);

      // Keep the tooltip glued to its beat without a React render.
      const tip = tipRef.current;
      const a = activeRef.current;
      if (tip && a) {
        const x = xOfTime(s, a.t, head);
        if (x < -4 || x > width + 4) {
          pinned.current = null;
          hover.current = null;
          setActiveBeat(null);
        } else {
          const left = x + 14 + TOOLTIP_W > width ? x - 14 - TOOLTIP_W : x + 14;
          tip.style.transform = `translate(${Math.round(Math.max(4, left))}px, 6px)`;
        }
      }
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [width, setActiveBeat, viewRef]);

  // ───────────── interaction ─────────────
  const beatAtX = (clientX: number): TimedBeat | null => {
    const s = scaleRef.current;
    const el = canvasRef.current;
    if (!s || !el) return null;
    const x = clientX - el.getBoundingClientRect().left;
    const items = props.current.source.beats.items;
    const i = nearestBeat(items, timeOfX(s, x, headRef.current), 14 / s.pxPerSec);
    return i >= 0 ? items[i] : null;
  };

  const onPointerDown = (e: PointerEvent<HTMLCanvasElement>) => {
    if (e.button !== 0) return;
    drag.current = { x: e.clientX, moved: false, id: e.pointerId };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: PointerEvent<HTMLCanvasElement>) => {
    const d = drag.current;
    const s = scaleRef.current;
    if (d && s) {
      const dx = e.clientX - d.x;
      if (!d.moved && Math.abs(dx) < 4) return;
      d.moved = true;
      d.x = e.clientX;
      if (onPan) onPan(dx / s.pxPerSec);
      return;
    }
    const b = beatAtX(e.clientX);
    hover.current = b ? b.sample : null;
    if (pinned.current === null) setActiveBeat(b);
  };
  const onPointerUp = (e: PointerEvent<HTMLCanvasElement>) => {
    const d = drag.current;
    drag.current = null;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    if (!d || d.moved) return;
    const b = beatAtX(e.clientX);
    if (b && pinned.current !== b.sample) {
      pinned.current = b.sample;
      setActiveBeat(b);
    } else {
      pinned.current = null;
      setActiveBeat(b && hover.current === b.sample ? b : null);
    }
  };
  const onPointerLeave = () => {
    hover.current = null;
    if (pinned.current === null) setActiveBeat(null);
  };

  const stepBeat = (dir: 1 | -1) => {
    const s = scaleRef.current;
    if (!s) return;
    const items = props.current.source.beats.items;
    const head = headRef.current;
    const [a, b] = visibleRange(items, head - s.windowS, head);
    if (b <= a) return;
    let i: number;
    const cur = pinned.current;
    const idx = cur === null ? -1 : items.findIndex((x) => x.sample === cur);
    if (idx < a || idx >= b) i = dir === 1 ? a : b - 1;
    else i = Math.min(b - 1, Math.max(a, idx + dir));
    pinned.current = items[i].sample;
    setActiveBeat(items[i]);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    switch (e.key) {
      case 'ArrowLeft': onPan?.(e.shiftKey ? 2 : 0.5); break;
      case 'ArrowRight': onPan?.(e.shiftKey ? -2 : -0.5); break;
      case ' ': onTogglePause?.(); break;
      case ',': case '[': stepBeat(-1); break;
      case '.': case ']': stepBeat(1); break;
      case 'Escape': pinned.current = null; hover.current = null; setActiveBeat(null); break;
      default: return;
    }
    e.preventDefault();
  };

  useEffect(() => {
    const el = canvasRef.current;
    if (!el || !onPan) return;
    const onWheel = (e: WheelEvent) => {
      const s = scaleRef.current;
      const dx = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.shiftKey ? e.deltaY : 0;
      if (!s || dx === 0) return;
      e.preventDefault();
      onPan(-dx / s.pxPerSec);
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [onPan]);

  return (
    <div
      ref={wrapRef}
      tabIndex={0}
      role="group"
      aria-label={`${ariaLabel ?? 'ECG waveform'}. Keys: space pauses, left and right arrows scroll, comma and period step through beats, escape clears the selection.`}
      onKeyDown={onKeyDown}
      className="relative w-full select-none overflow-hidden rounded-[3px] border border-line bg-[#06131F] focus-visible:outline-2"
      style={{ height: width > 0 ? cssH + 2 : undefined, minHeight: 120 }}
    >
      <canvas
        ref={canvasRef}
        role="img"
        aria-label={ariaLabel ?? 'ECG waveform with beat labels'}
        style={{ width: '100%', height: cssH, display: 'block', cursor: onPan ? 'grab' : 'crosshair', touchAction: 'pan-y' }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onPointerLeave={onPointerLeave}
      />
      <div ref={tipRef} className="pointer-events-none absolute left-0 top-0 z-10" style={{ width: TOOLTIP_W, visibility: active ? 'visible' : 'hidden' }}>
        {active && <BeatTooltip beat={active} pinned={pinned.current === active.sample} />}
      </div>
    </div>
  );
}

// ───────────── drawing (pure w.r.t. its arguments) ─────────────
function drawFrame(
  ctx: CanvasRenderingContext2D, s: EcgScale, H: number, band: number, head: number, source: EcgSource,
  showTruth: boolean, hoverSample: number | null, pinnedSample: number | null,
) {
  const W = s.widthPx;
  const plotH = s.heightPx;
  const ring = source.ring;
  const fs = ring.fs;
  const from = head - s.windowS;

  ctx.fillStyle = BG;
  ctx.fillRect(0, 0, W, H);

  // grid — attached to the paper, so it scrolls with the trace
  const v = verticalGridLines(s, head);
  const h = horizontalGridLines(s);
  ctx.lineWidth = 1;
  if (s.pxPerMm >= 3) {
    ctx.strokeStyle = MINOR;
    ctx.beginPath();
    for (const l of v) if (!l.major) { ctx.moveTo(l.x, 0); ctx.lineTo(l.x, plotH); }
    for (const l of h) if (!l.major) { const y = Math.round(l.y) + 0.5; ctx.moveTo(0, y); ctx.lineTo(W, y); }
    ctx.stroke();
  }
  ctx.strokeStyle = MAJOR;
  ctx.beginPath();
  for (const l of v) if (l.major) { ctx.moveTo(l.x, 0); ctx.lineTo(l.x, plotH); }
  for (const l of h) if (l.major) { const y = Math.round(l.y) + 0.5; ctx.moveTo(0, y); ctx.lineTo(W, y); }
  ctx.stroke();

  // time labels at whole seconds
  ctx.font = `10px ${MONO}`;
  ctx.fillStyle = FAINT;
  ctx.textAlign = 'left';
  ctx.textBaseline = 'alphabetic';
  const stepS = s.pxPerSec >= 70 ? 1 : s.pxPerSec >= 35 ? 2 : 5;
  for (let t = Math.ceil(from / stepS) * stepS; t <= head; t += stepS) {
    if (t < 0) continue;
    const x = xOfTime(s, t, head);
    ctx.fillRect(x, plotH - 6, 1, 6);
    if (x < W - 30) ctx.fillText(fmtClock(t), x + 3, plotH - 4);
  }

  // trace + gaps
  const i0 = Math.max(ring.start, Math.floor(from * fs) - 1);
  const i1 = Math.min(ring.end - 1, Math.ceil(head * fs) + 1);
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, 0, W, plotH);
  ctx.clip();
  if (!ring.isEmpty && i1 >= i0) {
    ctx.strokeStyle = TRACE;
    ctx.lineWidth = 1.5;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    ctx.beginPath();
    let pen = false;
    let gapStart = -1;
    const gaps: [number, number][] = [];
    for (let i = i0; i <= i1; i++) {
      const mv = ring.get(i);
      if (mv !== mv) {
        if (gapStart < 0) gapStart = i;
        pen = false;
        continue;
      }
      if (gapStart >= 0) { gaps.push([gapStart, i]); gapStart = -1; }
      const x = W - (head - i / fs) * s.pxPerSec;
      const y = s.baselinePx - mv * s.pxPerMv;
      if (pen) ctx.lineTo(x, y); else { ctx.moveTo(x, y); pen = true; }
    }
    if (gapStart >= 0) gaps.push([gapStart, i1 + 1]);
    ctx.stroke();
    for (const [a, b] of gaps) {
      const x0 = xOfTime(s, a / fs, head), x1 = xOfTime(s, b / fs, head);
      if (x1 - x0 < 2) continue;
      drawGap(ctx, x0, x1, plotH);
    }
  }
  if (ring.isEmpty || head < ring.startTime) {
    ctx.fillStyle = MUTED;
    ctx.font = `12px ${MONO}`;
    ctx.textAlign = 'center';
    ctx.fillText(ring.isEmpty ? 'No signal' : 'Beyond the stored history', W / 2, plotH / 2 - 16);
  }
  ctx.restore();

  // beats
  const items = source.beats.items;
  const [a, b] = visibleRange(items, from - 0.05, head + 0.05);
  ctx.textAlign = 'center';
  for (let k = a; k < b; k++) {
    const bt = items[k];
    const x = xOfTime(s, bt.t, head);
    const si = Math.round(bt.t * fs);
    const peak = ring.maxIn(si - 4, si + 4);
    const py = peak === peak ? yOfMv(s, peak) : s.baselinePx;
    const my = Math.min(plotH - 24, Math.max(22, py - 11));
    const st = BEAT_STYLE[bt.label] ?? BEAT_STYLE.Unknown;
    const isActive = bt.sample === pinnedSample || (pinnedSample === null && bt.sample === hoverSample);
    if (isActive) {
      ctx.strokeStyle = 'rgba(230,237,243,0.55)';
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(Math.round(x) + 0.5, 0);
      ctx.lineTo(Math.round(x) + 0.5, H);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.arc(x, my, 9, 0, Math.PI * 2);
      ctx.stroke();
    }
    drawBeatShape(ctx, bt.label, x, my, 4);
    ctx.font = `600 11px ${MONO}`;
    ctx.fillStyle = st.text;
    ctx.fillText(st.letter, x, my - 9);
  }

  // model / ground-truth rows
  if (showTruth && band > 0) {
    const top = plotH;
    ctx.fillStyle = '#0B1B2B';
    ctx.fillRect(0, top, W, band);
    ctx.fillStyle = MAJOR;
    ctx.fillRect(0, top, W, 1);
    ctx.fillRect(0, top + 3 + ROW_H, W, 1);
    const yModel = top + 3 + ROW_H / 2 + 4;
    const yTruth = yModel + ROW_H;
    ctx.font = `600 12px ${MONO}`;
    for (let k = a; k < b; k++) {
      const bt = items[k];
      const x = xOfTime(s, bt.t, head);
      if (x < 46) continue; // under the row captions
      const st = BEAT_STYLE[bt.label] ?? BEAT_STYLE.Unknown;
      ctx.fillStyle = st.text;
      ctx.fillText(st.letter, x, yModel);
      const ag = agreement(bt);
      if (ag === 'none') {
        ctx.fillStyle = FAINT;
        ctx.fillText('·', x, yTruth);
        continue;
      }
      const tst = BEAT_STYLE[bt.truth as BeatLabel] ?? BEAT_STYLE.Unknown;
      ctx.fillStyle = ag === 'mismatch' ? '#FFFFFF' : tst.text;
      ctx.fillText(tst.letter, x, yTruth);
      if (ag === 'mismatch') {
        // disagreement: boxed across both rows + a "≠" tick, so it does not rely on colour
        ctx.strokeStyle = '#FF8589';
        ctx.lineWidth = 1.5;
        ctx.strokeRect(Math.round(x - 8) + 0.5, top + 3.5, 16, band - 6);
        ctx.fillStyle = '#FF8589';
        ctx.beginPath();
        ctx.moveTo(x - 4, top); ctx.lineTo(x + 4, top); ctx.lineTo(x, top - 6); ctx.closePath();
        ctx.fill();
      }
    }
    // row captions (fixed)
    ctx.fillStyle = '#0B1B2B';
    ctx.fillRect(0, top + 1, 44, band - 1);
    ctx.fillStyle = MUTED;
    ctx.font = `10px ${MONO}`;
    ctx.textAlign = 'left';
    ctx.fillText('MODEL', 5, yModel - 1);
    ctx.fillText('TRUTH', 5, yTruth - 1);
    ctx.textAlign = 'center';
  }

  // calibration mark (1 mV × 0.2 s) and scale text: a compact fixed overlay in the top-left corner
  {
    const calW = 0.2 * s.pxPerSec, calH = s.pxPerMv;
    const text = `${s.speedMmS} mm/s  ${s.gainMmMv} mm/mV`;
    ctx.font = `10px ${MONO}`;
    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';
    const tw = ctx.measureText(text).width;
    const showPulse = calH <= plotH * 0.45;
    ctx.fillStyle = 'rgba(6,19,31,0.88)';
    ctx.fillRect(0, 0, tw + 12, 16);
    ctx.fillStyle = MUTED;
    ctx.fillText(text, 6, 11);
    if (showPulse) {
      const bx = 6, top = 22, baseY = top + calH;
      ctx.strokeStyle = 'rgba(169,184,198,0.75)'; // no backdrop: the pulse must not hide the trace under it
      ctx.lineWidth = 1.25;
      ctx.beginPath();
      ctx.moveTo(bx - 3, baseY); ctx.lineTo(bx, baseY); ctx.lineTo(bx, top); ctx.lineTo(bx + calW, top);
      ctx.lineTo(bx + calW, baseY); ctx.lineTo(bx + calW + 3, baseY);
      ctx.stroke();
      if (calW >= 24) { ctx.fillStyle = FAINT; ctx.fillText('1 mV', bx + 3, baseY - 4); }
    }
  }
}

function drawGap(ctx: CanvasRenderingContext2D, x0: number, x1: number, plotH: number) {
  ctx.save();
  ctx.beginPath();
  ctx.rect(x0, 0, x1 - x0, plotH);
  ctx.clip();
  ctx.fillStyle = 'rgba(201,130,10,0.10)';
  ctx.fillRect(x0, 0, x1 - x0, plotH);
  ctx.strokeStyle = 'rgba(201,130,10,0.45)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let x = x0 - plotH; x < x1; x += 9) { ctx.moveTo(x, plotH); ctx.lineTo(x + plotH, 0); }
  ctx.stroke();
  ctx.restore();
  if (x1 - x0 > 64) {
    ctx.fillStyle = '#EBA63A';
    ctx.font = `600 10px ${MONO}`;
    ctx.textAlign = 'center';
    ctx.fillText('GAP — no data', (x0 + x1) / 2, 14);
  }
}
