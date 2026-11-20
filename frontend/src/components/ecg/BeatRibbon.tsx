import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { visibleRange } from '../../live/beatMatch';
import type { EcgSource } from '../../live/liveFeed';
import { fmtClock } from '../../live/scale';
import { BEAT_STYLE, beatFill } from './beatStyle';

interface Props {
  source: EcgSource;
  /** Stream time at the right edge of the ribbon. */
  getHead: (nowMs: number) => number;
  spanS?: number;
  /** Visible range of the ECG panel, drawn as a bracket. */
  viewRef?: { current: { from: number; to: number } | null };
  /** Click / keyboard seek: centre the ECG view on this time. */
  onSeek?: (t: number) => void;
}

const BLOCK_H = 28;
const AXIS_H = 15;
const H = BLOCK_H + AXIS_H + 4;
const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';

/** Last 60 s, one block per beat, colour + pattern + height per class. */
export function BeatRibbon({ source, getHead, spanS = 60, viewRef, onSeek }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = useState(0);
  const p = useRef({ source, getHead, spanS, viewRef });
  p.current = { source, getHead, spanS, viewRef };
  const headRef = useRef(0);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver((e) => setWidth(Math.floor(e[0].contentRect.width)));
    ro.observe(el);
    setWidth(Math.floor(el.clientWidth));
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || width <= 0) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    let raf = 0;
    let last = { head: NaN, ver: -1, vf: NaN, dpr: 0 };
    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      const { source: src, getHead: gh, spanS: span, viewRef: vr } = p.current;
      const dpr = Math.min(3, window.devicePixelRatio || 1);
      if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(H * dpr)) {
        canvas.width = Math.round(width * dpr);
        canvas.height = Math.round(H * dpr);
        last.head = NaN;
      }
      const head = gh(now);
      const view = vr?.current ?? null;
      if (head === last.head && src.beats.version === last.ver && (view?.from ?? NaN) === last.vf && dpr === last.dpr) return;
      last = { head, ver: src.beats.version, vf: view?.from ?? NaN, dpr };
      headRef.current = head;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, H);
      const pxPerS = width / span;
      const from = head - span;
      const X = (t: number) => (t - from) * pxPerS;

      // track
      ctx.fillStyle = '#FFFFFF';
      ctx.fillRect(0, 0, width, BLOCK_H + 4);

      // axis: ticks every 10 s of stream time
      ctx.font = `10px ${MONO}`;
      ctx.textBaseline = 'alphabetic';
      ctx.textAlign = 'left';
      for (let t = Math.ceil(from / 10) * 10; t <= head; t += 10) {
        if (t < 0) continue;
        const x = X(t);
        ctx.fillStyle = 'rgba(0,184,107,0.16)';
        ctx.fillRect(Math.round(x), 0, 1, BLOCK_H + 8);
        ctx.fillStyle = '#64748B';
        if (x < width - 28) ctx.fillText(fmtClock(t), x + 3, H - 2);
      }

      const items = src.beats.items;
      const [a, b] = visibleRange(items, from - 2, head);
      for (let k = a; k < b; k++) {
        const bt = items[k];
        const next = k + 1 < items.length ? items[k + 1].t : head;
        const x0 = X(bt.t);
        const x1 = X(Math.min(next, bt.t + 1.6, head));
        const w = Math.max(2, x1 - x0 - 1);
        const st = BEAT_STYLE[bt.label] ?? BEAT_STYLE.Unknown;
        const h = Math.round(BLOCK_H * st.height);
        const y = 2 + BLOCK_H - h;
        if (st.pattern === 'hollow') {
          ctx.strokeStyle = st.color;
          ctx.lineWidth = 1;
          ctx.setLineDash([2, 2]);
          ctx.strokeRect(Math.round(x0) + 0.5, y + 0.5, Math.max(1, Math.round(w) - 1), h - 1);
          ctx.setLineDash([]);
        } else {
          ctx.fillStyle = beatFill(ctx, bt.label, dpr);
          ctx.fillRect(Math.round(x0), y, Math.round(w), h);
          if (st.pattern !== 'solid') {
            ctx.strokeStyle = st.color;
            ctx.lineWidth = 1;
            ctx.strokeRect(Math.round(x0) + 0.5, y + 0.5, Math.max(1, Math.round(w) - 1), h - 1);
          }
        }
      }

      // ECG viewport bracket
      if (view) {
        const x0 = Math.max(0.5, X(view.from)), x1 = Math.min(width - 0.5, X(view.to));
        if (x1 > 0 && x0 < width) {
          ctx.strokeStyle = 'rgba(11,31,58,0.45)';
          ctx.lineWidth = 1;
          ctx.strokeRect(Math.round(x0) + 0.5, 0.5, Math.max(2, Math.round(x1 - x0) - 1), BLOCK_H + 3);
        }
      }
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [width]);

  const seekAt = (clientX: number) => {
    const el = canvasRef.current;
    if (!el || !onSeek || width <= 0) return;
    const x = clientX - el.getBoundingClientRect().left;
    onSeek(headRef.current - spanS + (x / width) * spanS);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLCanvasElement>) => {
    if (!onSeek) return;
    const step = e.shiftKey ? spanS / 4 : spanS / 10;
    if (e.key === 'ArrowLeft') {
      onSeek(headRef.current - spanS / 2 - step);
      e.preventDefault();
    } else if (e.key === 'ArrowRight') {
      onSeek(headRef.current - spanS / 2 + step);
      e.preventDefault();
    } else if (e.key === 'Home') {
      onSeek(headRef.current - spanS);
      e.preventDefault();
    } else if (e.key === 'End') {
      onSeek(headRef.current);
      e.preventDefault();
    }
  };

  return (
    <div ref={wrapRef} className="w-full">
      <canvas
        ref={canvasRef}
        role={onSeek ? 'slider' : 'img'}
        tabIndex={onSeek ? 0 : undefined}
        aria-label={`Beat ribbon: the last ${spanS} seconds, one block per beat. Block colour, pattern and height encode the beat class.${onSeek ? ' Use arrow keys to move the ECG view.' : ''}`}
        aria-valuemin={0}
        aria-valuemax={spanS}
        style={{ width: '100%', height: H, display: 'block', cursor: onSeek ? 'pointer' : 'default' }}
        onClick={(e) => seekAt(e.clientX)}
        onKeyDown={onKeyDown}
        title={onSeek ? 'Click or use arrow keys to move the ECG view to this moment' : undefined}
      />
    </div>
  );
}
