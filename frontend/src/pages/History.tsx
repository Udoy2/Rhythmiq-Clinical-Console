import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api, qk } from '../api/endpoints';
import { TREND_RANGES, type Alert, type Session, type TrendRange, type Trends } from '../api/types';
import { TrendChart, type TrendPoint } from '../components/charts/TrendChart';
import { TIER_STYLE, tierLabel } from '../components/ecg/beatStyle';
import { STATE_LABEL } from '../components/live/AlertsPanel';
import { Badge, Banner, Card, EmptyState, QueryState, Segmented, ShapeGlyph } from '../components/ui';
import { fmtDateTime, fmtDuration, fmtNum, fmtPct, parseDate } from '../lib/format';
import { replayBadgeText } from './LiveConsole';
import { usePatient } from './PatientLayout';

const RANGE_MS: Record<TrendRange, number> = { '24h': 864e5, '7d': 7 * 864e5, '30d': 30 * 864e5, '90d': 90 * 864e5 };
const RANGE_LABEL: Record<TrendRange, string> = { '24h': '24 h', '7d': '7 d', '30d': '30 d', '90d': '90 d' };

function useTimeFormat(range: TrendRange) {
  return useMemo(() => {
    const tick = new Intl.DateTimeFormat(undefined, range === '24h' ? { hour: '2-digit', minute: '2-digit', hour12: false }
      : { month: 'short', day: '2-digit' });
    const full = new Intl.DateTimeFormat(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
    return { tick: (t: number) => tick.format(new Date(t)), full: (t: number) => full.format(new Date(t)) };
  }, [range]);
}

export function sessionDuration(s: Session): number | null {
  if (typeof s.duration_s === 'number') return s.duration_s;
  if (typeof s.summary?.duration_s === 'number') return s.summary.duration_s;
  const a = parseDate(s.started_at), b = parseDate(s.ended_at);
  return a && b ? (b.getTime() - a.getTime()) / 1000 : null;
}

export default function HistoryPage() {
  const { patient } = usePatient();
  const [range, setRange] = useState<TrendRange>('7d');
  const [hover, setHover] = useState<number | null>(null);
  const [params] = useSearchParams();
  const nav = useNavigate();
  const fmt = useTimeFormat(range);

  const trends = useQuery({ queryKey: qk.trends(patient.id, range), queryFn: () => api.trends(patient.id, range) });
  const sessions = useQuery({ queryKey: qk.sessions(patient.id), queryFn: () => api.sessions(patient.id) });
  const alerts = useQuery({ queryKey: qk.alerts({ patient_id: patient.id }), queryFn: () => api.alerts({ patient_id: patient.id }) });
  useEffect(() => setHover(null), [range]);

  // A citation link (?at=ISO) opens the stored session that covers that moment, at that moment.
  const at = params.get('at');
  const [citeMiss, setCiteMiss] = useState(false);
  useEffect(() => {
    if (!at || !sessions.data) return;
    const t = parseDate(at)?.getTime();
    if (t === undefined) return;
    const hit = sessions.data.find((s) => {
      const a = parseDate(s.started_at)?.getTime();
      const d = sessionDuration(s);
      return a !== undefined && t >= a - 1000 && (d === null ? s.active : t <= a + d * 1000 + 1000);
    });
    if (hit) {
      const a = parseDate(hit.started_at)?.getTime() ?? t;
      nav(`/patients/${patient.id}/sessions/${hit.id}?t=${Math.max(0, (t - a) / 1000).toFixed(1)}`, { replace: true });
    } else setCiteMiss(true);
  }, [at, sessions.data, nav, patient.id]);

  return (
    <div className="space-y-3">
      {citeMiss && <Banner tone="info" title="The cited moment is not inside a stored session.">Showing the patient history instead ({fmtDateTime(at)}).</Banner>}
      <Card
        title="Trends"
        right={<Segmented label="Date range" value={range} options={TREND_RANGES} onChange={setRange} format={(r) => RANGE_LABEL[r]} size="md" />}
      >
        <QueryState query={trends} loadingLabel="Loading trends…" isEmpty={(t: Trends) => !t.buckets.some((b) => b.hr_bpm !== null || b.risk_score !== null || b.v_burden_pct !== null)}
          empty={<EmptyState title="No trend data in this range">Trends appear after the patient has recorded sessions.</EmptyState>}>
          {(t) => <TrendCharts trends={t} hover={hover} onHover={setHover} fmt={fmt} />}
        </QueryState>
      </Card>

      <Card title="Alert and episode timeline" right={<span className="text-xs text-fg-muted">last {RANGE_LABEL[range]}</span>}>
        <QueryState query={alerts} isEmpty={() => false}>
          {(list) => <Timeline alerts={list} range={range} />}
        </QueryState>
      </Card>

      <Card title="Recorded sessions" bodyClass="p-0">
        <QueryState query={sessions} loadingLabel="Loading sessions…"
          empty={<EmptyState title="No sessions recorded yet">A session is stored each time a device or a replay streams for this patient.</EmptyState>}>
          {(list) => (
            <div className="overflow-x-auto">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Started</th><th scope="col">Source</th><th scope="col" className="text-right">Duration</th>
                    <th scope="col" className="text-right">Beats</th><th scope="col" className="text-right">Mean HR</th>
                    <th scope="col" className="text-right">S burden</th><th scope="col" className="text-right">V burden</th>
                    <th scope="col" className="text-right">Alerts</th><th scope="col"><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody>
                  {[...list].sort((a, b) => (b.started_at ?? '').localeCompare(a.started_at ?? '')).map((s) => {
                    const sm = s.summary ?? {};
                    return (
                      <tr key={s.id} className="hover:bg-ink-800">
                        <td className="num whitespace-nowrap">{fmtDateTime(s.started_at)} {s.active && <Badge tone="teal" className="ml-1">ACTIVE</Badge>}</td>
                        <td>{s.source === 'replay' ? <Badge tone="amber">{replayBadgeText(s)}</Badge> : <span className="text-fg-muted">Device {s.device_id ?? ''}</span>}</td>
                        <td className="num text-right">{fmtDuration(sessionDuration(s))}</td>
                        <td className="num text-right">{fmtNum(sm.beats as number | null)}</td>
                        <td className="num text-right">{fmtNum((sm.mean_hr_bpm ?? sm.hr_bpm) as number | null)}</td>
                        <td className="num text-right">{fmtPct(sm.s_burden_pct as number | null)}</td>
                        <td className="num text-right">{fmtPct(sm.v_burden_pct as number | null)}</td>
                        <td className="num text-right">{fmtNum(sm.alerts as number | null)}</td>
                        <td className="text-right">
                          <Link to={`/patients/${patient.id}/sessions/${s.id}`} className="text-xs font-medium text-teal-text hover:underline">
                            {s.active ? 'Details' : 'Replay'}<span className="sr-only"> session started {fmtDateTime(s.started_at)}</span>
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </QueryState>
      </Card>
    </div>
  );
}

const EMPTY = { hr: null, sdnn: null, rmssd: null, s: null, v: null, risk: null };

/**
 * Buckets → chart points. The API may return only buckets that have data plus explicit gaps, so an empty
 * point is inserted at every gap start (lines must never bridge a gap) and at the range bounds (so the x-axis
 * covers the whole requested range, not just the part with data).
 */
export function buildTrendPoints(trends: Trends, gaps: { start: number; end: number }[]): TrendPoint[] {
  const pts: TrendPoint[] = [];
  for (const b of trends.buckets) {
    const t = parseDate(b.t)?.getTime();
    if (t === undefined) continue;
    pts.push({ t, values: { hr: b.hr_bpm, sdnn: b.sdnn_ms, rmssd: b.rmssd_ms, s: b.s_burden_pct, v: b.v_burden_pct, risk: b.risk_score } });
  }
  const have = new Set(pts.map((p) => p.t));
  const addEmpty = (t: number | undefined) => { if (t !== undefined && Number.isFinite(t) && !have.has(t)) { have.add(t); pts.push({ t, values: { ...EMPTY } }); } };
  gaps.forEach((g) => addEmpty(g.start));
  addEmpty(parseDate(trends.from)?.getTime());
  addEmpty(parseDate(trends.to)?.getTime());
  return pts.sort((a, b) => a.t - b.t);
}

function TrendCharts({ trends, hover, onHover, fmt }: { trends: Trends; hover: number | null; onHover: (i: number | null) => void; fmt: { tick: (t: number) => string; full: (t: number) => string } }) {
  const gaps = useMemo(() => trends.gaps
    .map((g) => ({ start: parseDate(g.start)?.getTime() ?? NaN, end: parseDate(g.end)?.getTime() ?? NaN }))
    .filter((g) => Number.isFinite(g.start) && Number.isFinite(g.end)), [trends]);
  const points = useMemo(() => buildTrendPoints(trends, gaps), [trends, gaps]);
  const common = { points, gaps, hover, onHover, formatTime: fmt.full, formatTick: fmt.tick };

  return (
    <div>
      <div className="grid grid-cols-1 gap-x-4 gap-y-4 lg:grid-cols-2">
        <TrendChart title="Heart rate" unit="bpm" series={[{ key: 'hr', label: 'HR', color: '#00A896' }]} {...common} />
        <TrendChart title="Risk score" unit="of 100" domain={[0, 100]} series={[{ key: 'risk', label: 'Risk', color: '#A9B8C6' }]} {...common} />
        <TrendChart title="V burden" unit="%" includeZero digits={1} series={[{ key: 'v', label: 'V burden', color: '#E0484D' }]} {...common} />
        <TrendChart title="S burden" unit="%" includeZero digits={1} series={[{ key: 's', label: 'S burden', color: '#C9820A' }]} {...common} />
        <div className="lg:col-span-2">
          <TrendChart title="Heart-rate variability" unit="ms" includeZero
            series={[{ key: 'sdnn', label: 'SDNN', color: '#00A896' }, { key: 'rmssd', label: 'RMSSD', color: '#8E7CF0', dashed: true }]} {...common} />
        </div>
      </div>
      <p className="mt-2 text-xs text-fg-muted">
        Hatched areas are periods without recorded data ({gaps.length} gap{gaps.length === 1 ? '' : 's'} in this range). Lines are never drawn across missing data.
      </p>
      <details className="mt-2">
        <summary className="cursor-pointer text-xs text-teal-text">Show the data as a table</summary>
        <div className="mt-2 max-h-72 overflow-auto rounded border border-line">
          <table>
            <thead><tr><th scope="col">Time</th><th scope="col" className="text-right">HR bpm</th><th scope="col" className="text-right">SDNN ms</th><th scope="col" className="text-right">RMSSD ms</th>
              <th scope="col" className="text-right">S %</th><th scope="col" className="text-right">V %</th><th scope="col" className="text-right">Risk</th></tr></thead>
            <tbody>
              {points.map((p) => (
                <tr key={p.t}>
                  <td className="num whitespace-nowrap">{fmt.full(p.t)}</td>
                  {(['hr', 'sdnn', 'rmssd', 's', 'v', 'risk'] as const).map((k) => (
                    <td key={k} className="num text-right">{typeof p.values[k] === 'number' ? (p.values[k] as number).toFixed(k === 's' || k === 'v' ? 1 : 0) : <span className="text-fg-faint">no data</span>}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

function Timeline({ alerts, range }: { alerts: Alert[]; range: TrendRange }) {
  const now = Date.now();
  const from = now - RANGE_MS[range];
  const inRange = alerts
    .map((a) => ({ a, t: parseDate(a.created_at)?.getTime() ?? NaN }))
    .filter((x) => x.t >= from && x.t <= now)
    .sort((x, y) => y.t - x.t);
  if (inRange.length === 0) return <p className="py-2 text-sm text-fg-muted">No alerts were raised in this period.</p>;
  return (
    <div>
      <div className="relative mb-3 h-9 rounded-[3px] border border-line bg-[#081724]" role="img" aria-label={`${inRange.length} alerts on a time axis; details are in the list below`}>
        {inRange.map(({ a, t }) => {
          const st = TIER_STYLE[a.tier] ?? TIER_STYLE.amber;
          return (
            <Link key={a.id} to={`/alerts/${a.id}`} title={`${tierLabel(a.tier)}: ${a.title} — ${fmtDateTime(a.created_at)}`}
              className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2 rounded p-1 hover:bg-ink-600" style={{ left: `${Math.min(98.5, Math.max(1.5, ((t - from) / (now - from)) * 100))}%` }}>
              <ShapeGlyph shape={st.shape} color={st.color} size={14} />
              <span className="sr-only">{tierLabel(a.tier)}: {a.title}</span>
            </Link>
          );
        })}
        <span className="num absolute bottom-0 left-1 text-2xs text-fg-faint">{fmtDateTime(new Date(from).toISOString())}</span>
        <span className="absolute bottom-0 right-1 text-2xs text-fg-faint">now</span>
      </div>
      <ul className="divide-y divide-line/60">
        {inRange.map(({ a }) => {
          const st = TIER_STYLE[a.tier] ?? TIER_STYLE.amber;
          return (
            <li key={a.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5 text-sm">
              <span className="num w-36 shrink-0 text-xs text-fg-muted">{fmtDateTime(a.created_at)}</span>
              <span className="inline-flex w-36 shrink-0 items-center gap-1.5 text-xs font-medium" style={{ color: st.text }}>
                <ShapeGlyph shape={st.shape} color={st.color} size={11} />{tierLabel(a.tier)}
              </span>
              <Link to={`/alerts/${a.id}`} className="min-w-0 flex-1 truncate font-medium text-fg hover:underline">{a.title}</Link>
              <span className="text-xs text-fg-muted">{STATE_LABEL[a.state] ?? a.state}</span>
              <Link to={`/patients/${a.patient_id}/sessions/${a.session_id}?t=${Math.max(0, a.evidence?.start_s ?? 0).toFixed(1)}`} className="text-xs text-teal-text hover:underline">Replay session</Link>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
