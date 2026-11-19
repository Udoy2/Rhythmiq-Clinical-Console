import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';

export interface TrendSeries { key: string; label: string; color: string; dashed?: boolean }
export interface TrendPoint { t: number; values: Record<string, number | null | undefined> }
export interface TrendGapMs { start: number; end: number }

interface Props {
  title: string;
  unit: string;
  series: TrendSeries[];
  points: TrendPoint[];
  gaps: TrendGapMs[];
  /** Fixed y-domain; otherwise derived from the data (always including `includeZero`). */
  domain?: [number, number];
  includeZero?: boolean;
  digits?: number;
  height?: number;
  /** Shared crosshair across small multiples (index into points). */
  hover: number | null;
  onHover: (i: number | null) => void;
  formatTime: (t: number) => string;
  formatTick: (t: number) => string;
}

const M = { l: 44, r: 12, t: 8, b: 20 };
const INK = '#10243E', MUTED = '#64748B', FAINT = '#94a3b8', GRID = 'rgba(0,184,107,0.14)', PAPER = '#FFFFFF', GAP = '#d97706';

function niceStep(span: number, target: number): number {
  const raw = span / Math.max(1, target);
  const p = 10 ** Math.floor(Math.log10(raw || 1));
  const f = raw / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
}

/** Split a series into runs of consecutive non-null points so that missing data shows as a break, not a line. */
export function runs(points: TrendPoint[], key: string): number[][] {
  const out: number[][] = [];
  let cur: number[] = [];
  points.forEach((p, i) => {
    const v = p.values[key];
    if (typeof v === 'number' && Number.isFinite(v)) cur.push(i);
    else if (cur.length) { out.push(cur); cur = []; }
  });
  if (cur.length) out.push(cur);
  return out;
}

export function TrendChart({
  title, unit, series, points, gaps, domain, includeZero, digits = 0, height = 132, hover, onHover, formatTime, formatTick,
}: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver((e) => setW(Math.floor(e[0].contentRect.width)));
    ro.observe(el);
    setW(Math.floor(el.clientWidth));
    return () => ro.disconnect();
  }, []);

  const geo = useMemo(() => {
    const t0 = points.length ? points[0].t : 0;
    const t1 = points.length ? points[points.length - 1].t : 1;
    let lo = Infinity, hi = -Infinity;
    for (const p of points) for (const s of series) {
      const v = p.values[s.key];
      if (typeof v === 'number' && Number.isFinite(v)) { if (v < lo) lo = v; if (v > hi) hi = v; }
    }
    const has = lo !== Infinity;
    if (domain) [lo, hi] = domain;
    else if (!has) { lo = 0; hi = 1; }
    else {
      if (includeZero) lo = Math.min(0, lo);
      const pad = (hi - lo || Math.abs(hi) || 1) * 0.12;
      hi += pad;
      if (!includeZero) lo -= pad;
    }
    const step = niceStep(hi - lo, 3);
    const ticks: number[] = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) ticks.push(Math.round(v * 1e6) / 1e6);
    const pw = Math.max(1, w - M.l - M.r), ph = height - M.t - M.b;
    const x = (t: number) => M.l + (t1 === t0 ? pw / 2 : ((t - t0) / (t1 - t0)) * pw);
    const y = (v: number) => M.t + ph - ((v - lo) / (hi - lo || 1)) * ph;
    return { t0, t1, lo, hi, ticks, pw, ph, x, y, has };
  }, [points, series, domain, includeZero, w, height]);

  const xTicks = useMemo(() => {
    const n = Math.max(2, Math.min(7, Math.floor(geo.pw / 110)));
    return Array.from({ length: n }, (_, i) => geo.t0 + ((geo.t1 - geo.t0) * i) / (n - 1));
  }, [geo]);

  const indexAt = (clientX: number): number | null => {
    const el = wrap.current;
    if (!el || points.length === 0) return null;
    const px = clientX - el.getBoundingClientRect().left;
    const t = geo.t0 + ((px - M.l) / geo.pw) * (geo.t1 - geo.t0);
    let best = 0, bd = Infinity;
    for (let i = 0; i < points.length; i++) { const d = Math.abs(points[i].t - t); if (d < bd) { bd = d; best = i; } }
    return best;
  };
  const onKey = (e: KeyboardEvent) => {
    if (!points.length) return;
    if (e.key === 'ArrowRight') onHover(Math.min(points.length - 1, (hover ?? -1) + 1));
    else if (e.key === 'ArrowLeft') onHover(Math.max(0, (hover ?? points.length) - 1));
    else if (e.key === 'Escape') onHover(null);
    else return;
    e.preventDefault();
  };

  const hp = hover !== null ? points[hover] : null;
  const hx = hp ? geo.x(hp.t) : 0;
  const tipLeft = hp ? (hx > w - 190 ? hx - 178 : hx + 10) : 0;
  const patId = `gap-${title.replace(/\W/g, '')}`;

  return (
    <figure className="min-w-0">
      <figcaption className="mb-1 flex flex-wrap items-baseline justify-between gap-x-3">
        <span className="text-sm font-semibold text-fg">{title} <span className="font-normal text-fg-muted">({unit})</span></span>
        {series.length > 1 && (
          <span className="flex flex-wrap gap-x-3 text-xs text-fg-muted">
            {series.map((s) => (
              <span key={s.key} className="inline-flex items-center gap-1.5">
                <svg width="18" height="6" aria-hidden="true"><line x1="0" y1="3" x2="18" y2="3" stroke={s.color} strokeWidth="2" strokeDasharray={s.dashed ? '5 3' : undefined} /></svg>
                {s.label}
              </span>
            ))}
          </span>
        )}
      </figcaption>
      <div
        ref={wrap}
        className="relative rounded-sm border border-line bg-white focus-visible:outline-2"
        tabIndex={0}
        role="group"
        aria-label={`${title} trend chart. Use left and right arrow keys to read values.`}
        onKeyDown={onKey}
        onPointerMove={(e) => onHover(indexAt(e.clientX))}
        onPointerLeave={() => onHover(null)}
        onBlur={() => onHover(null)}
      >
        {w > 0 && (
          <svg width={w} height={height} role="img" aria-label={`${title} over time`} className="block">
            <defs>
              <pattern id={patId} width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <line x1="0" y1="0" x2="0" y2="7" stroke="rgba(201,130,10,0.5)" strokeWidth="1.5" />
              </pattern>
            </defs>
            {geo.ticks.map((v) => (
              <g key={v}>
                <line x1={M.l} x2={w - M.r} y1={Math.round(geo.y(v)) + 0.5} y2={Math.round(geo.y(v)) + 0.5} stroke={GRID} />
                <text x={M.l - 6} y={geo.y(v) + 3.5} textAnchor="end" fontSize="10" fill={FAINT} className="num">{v}</text>
              </g>
            ))}
            {xTicks.map((t, i) => (
              <text key={i} x={geo.x(t)} y={height - 6} fontSize="10" fill={FAINT} className="num"
                textAnchor={i === 0 ? 'start' : i === xTicks.length - 1 ? 'end' : 'middle'}>{formatTick(t)}</text>
            ))}
            {gaps.map((g, i) => {
              const a = Math.max(M.l, geo.x(g.start)), b = Math.min(w - M.r, geo.x(g.end));
              if (b - a < 1) return null;
              return (
                <g key={i}>
                  <rect x={a} y={M.t} width={b - a} height={geo.ph} fill="rgba(201,130,10,0.07)" />
                  <rect x={a} y={M.t} width={b - a} height={geo.ph} fill={`url(#${patId})`} opacity="0.5" />
                  {b - a > 52 && <text x={(a + b) / 2} y={M.t + 12} textAnchor="middle" fontSize="10" fill={GAP}>no data</text>}
                </g>
              );
            })}
            {series.map((s) => runs(points, s.key).map((run, ri) => (
              run.length === 1
                ? <circle key={`${s.key}-${ri}`} cx={geo.x(points[run[0]].t)} cy={geo.y(points[run[0]].values[s.key] as number)} r="2" fill={s.color} />
                : <polyline key={`${s.key}-${ri}`} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round"
                    strokeDasharray={s.dashed ? '5 3' : undefined}
                    points={run.map((i) => `${geo.x(points[i].t).toFixed(1)},${geo.y(points[i].values[s.key] as number).toFixed(1)}`).join(' ')} />
            )))}
            {hp && (
              <g>
                <line x1={hx} x2={hx} y1={M.t} y2={M.t + geo.ph} stroke={MUTED} strokeDasharray="3 3" />
                {series.map((s) => {
                  const v = hp.values[s.key];
                  return typeof v === 'number' ? <circle key={s.key} cx={hx} cy={geo.y(v)} r="4" fill={s.color} stroke={PAPER} strokeWidth="2" /> : null;
                })}
              </g>
            )}
            {!geo.has && <text x={w / 2} y={height / 2} textAnchor="middle" fontSize="12" fill={MUTED}>No data in this range</text>}
          </svg>
        )}
        {hp && (
          <div className="pointer-events-none absolute top-1 z-10 w-[168px] rounded border border-line bg-white px-2 py-1.5 text-xs shadow-md" style={{ left: tipLeft }} role="status">
            <div className="num text-fg-muted">{formatTime(hp.t)}</div>
            {series.map((s) => {
              const v = hp.values[s.key];
              return (
                <div key={s.key} className="flex items-center justify-between gap-2">
                  <span className="inline-flex items-center gap-1.5 text-fg-muted">
                    <svg width="10" height="6" aria-hidden="true"><line x1="0" y1="3" x2="10" y2="3" stroke={s.color} strokeWidth="2" strokeDasharray={s.dashed ? '3 2' : undefined} /></svg>
                    {s.label}
                  </span>
                  <span className="num font-semibold" style={{ color: INK }}>{typeof v === 'number' ? `${v.toFixed(digits)} ${unit}` : 'no data'}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </figure>
  );
}
