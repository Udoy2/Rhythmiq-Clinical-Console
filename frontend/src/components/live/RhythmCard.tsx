import type { Finding, RhythmWindow, SessionMsg } from '../../api/types';
import { fmtNum, fmtPct } from '../../lib/format';
import { fmtClock } from '../../live/scale';
import { TIER_STYLE } from '../ecg/beatStyle';
import { BeatChip, Card, Mono, ShapeGlyph, Stat } from '../ui';

export function FindingChip({ f }: { f: Finding }) {
  const st = TIER_STYLE[f.severity] ?? TIER_STYLE.amber;
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded border px-1.5 py-0.5 text-xs font-medium"
      style={{ borderColor: st.color, background: st.bg, color: '#E6EDF3' }}
      title={`${f.detail || f.label} — rule ${f.rule_id}, ${st.name} (${st.meaning})`}
    >
      <ShapeGlyph shape={st.shape} color={st.color} size={10} />
      {f.label}
      <span className="text-2xs font-normal" style={{ color: st.text }}>{st.meaning}</span>
    </span>
  );
}

export function RhythmCard({ window: w, session, suppressed }: { window: RhythmWindow | null; session: SessionMsg | null; suppressed: boolean }) {
  return (
    <Card
      title="Rhythm status"
      right={w ? <span className="num text-xs text-fg-muted">window {fmtClock(w.start_s)}–{fmtClock(w.end_s)}</span> : undefined}
    >
      <div className="flex items-end justify-between gap-4">
        <div>
          <div className="label">Heart rate</div>
          <div className="flex items-baseline gap-1.5">
            <span className="num text-5xl font-semibold leading-none text-fg">{fmtNum(w?.hr_bpm, 0)}</span>
            <span className="text-sm text-fg-muted">bpm</span>
          </div>
          <div className="mt-1 text-2xs text-fg-faint">Median over the last 10 s window</div>
        </div>
        <div className="text-right">
          <div className="label">Signal quality</div>
          <div className="num text-lg font-semibold">{w ? fmtNum(w.sqi, 2) : '—'}</div>
        </div>
      </div>

      <div className="mt-3">
        <div className="label mb-1.5">Current findings</div>
        {!w ? (
          <p className="text-sm text-fg-muted">Waiting for the first 10 s rhythm window.</p>
        ) : suppressed ? (
          <p className="text-sm text-amber-text">Findings are not evaluated while the signal quality is degraded.</p>
        ) : w.findings.length === 0 ? (
          <p className="text-sm text-fg-muted">No rhythm findings in this window.</p>
        ) : (
          <ul className="flex flex-wrap gap-1.5">
            {w.findings.map((f, i) => <li key={`${f.rule_id}-${i}`}><FindingChip f={f} /></li>)}
          </ul>
        )}
      </div>

      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 border-t border-line pt-2 text-xs">
        <dt className="text-fg-muted">Rules</dt>
        <dd className="text-right"><Mono>{w?.rule_version ?? session?.rule_version ?? '—'}</Mono></dd>
        <dt className="text-fg-muted">Model</dt>
        <dd className="text-right"><Mono>{session ? (session.model_loaded ? session.model_version : 'not loaded (rules only)') : '—'}</Mono></dd>
      </dl>
    </Card>
  );
}

export function VitalsStrip({ window: w }: { window: RhythmWindow | null }) {
  const c = w?.beat_counts;
  return (
    <section aria-label="Vitals" className="panel grid grid-cols-3 gap-x-4 gap-y-3 p-3 lg:grid-cols-6">
      <Stat label="HR" value={fmtNum(w?.hr_bpm, 0)} unit="bpm" />
      <Stat label="SDNN" value={fmtNum(w?.sdnn_ms, 0)} unit="ms" sub="HRV, normal-to-normal" />
      <Stat label="RMSSD" value={fmtNum(w?.rmssd_ms, 0)} unit="ms" sub="HRV, beat-to-beat" />
      <Stat label="S burden" value={w ? fmtPct(w.s_burden_pct) : '—'} sub="supraventricular ectopic" tone={w && w.s_burden_pct >= 1 ? '#EBA63A' : undefined} />
      <Stat label="V burden" value={w ? fmtPct(w.v_burden_pct) : '—'} sub="ventricular ectopic" tone={w && w.v_burden_pct >= 1 ? '#FF8589' : undefined} />
      <div className="min-w-0">
        <div className="label">Beats in window</div>
        {c ? (
          <ul className="mt-1 flex flex-wrap gap-x-2.5 gap-y-0.5">
            {(['N', 'S', 'V', 'F', 'Unknown'] as const).map((l) => (
              <li key={l} className="inline-flex items-center gap-1"><BeatChip label={l} /><span className="num text-sm text-fg">{c[l] ?? 0}</span></li>
            ))}
          </ul>
        ) : <div className="num text-xl font-semibold leading-7">—</div>}
      </div>
    </section>
  );
}
