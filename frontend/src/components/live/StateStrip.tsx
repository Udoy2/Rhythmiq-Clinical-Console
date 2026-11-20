import type { Risk } from '../../api/types';
import { fmtNum } from '../../lib/format';
import { TIER_STYLE, tierTitle } from '../ecg/beatStyle';
import { ShapeGlyph } from '../ui';

/** Urgent and Critical flip to a solid fill so they are the loudest thing on the page. `stale` (stream down or alerts suppressed) shows the last window muted, never tinted. */
export function StateStrip({ risk, hr, openAlerts, stale }: { risk: Risk | null; hr: number | null | undefined; openAlerts: number; stale: boolean }) {
  const st = risk ? TIER_STYLE[risk.tier] : null;
  const loud = !stale && !!risk && (risk.tier === 'orange' || risk.tier === 'red');
  const tint = !stale && st;
  return (
    <section
      aria-label="Current state"
      className="mb-3 flex flex-wrap items-center gap-x-8 gap-y-2 rounded border-2 px-4 py-3 text-fg"
      style={{ borderColor: tint ? st.color : 'var(--rq-border)', background: loud ? st!.text : tint ? st.bg : undefined, color: loud ? '#fff' : undefined }}
    >
      <div className="flex items-center gap-2" title={risk ? tierTitle(risk.tier) : undefined}>
        {st && <ShapeGlyph shape={st.shape} color={loud ? '#fff' : stale ? '#6A6158' : st.color} size={24} hollow={stale} />}
        <span aria-live="polite" className="font-display text-2xl font-bold leading-none" style={{ color: loud ? undefined : stale ? undefined : st?.text }}>
          {st ? (stale ? `Last window: ${st.meaning}` : st.meaning) : 'Waiting for the first rhythm window'}
        </span>
      </div>
      <div className="flex items-baseline gap-1.5" title="Median heart rate over the last 10 s window">
        <span className="text-xs font-semibold">HR</span>
        <span className={`num text-2xl font-semibold leading-none${stale ? ' text-fg-faint' : ''}`}>{fmtNum(hr, 0)}</span>
        <span className="text-sm">bpm</span>
      </div>
      <div className="flex items-baseline gap-1.5">
        <span className="text-xs font-semibold">Open alerts</span>
        <span className="num text-2xl font-semibold leading-none">{openAlerts}</span>
      </div>
    </section>
  );
}
