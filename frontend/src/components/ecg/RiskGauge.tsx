import type { Risk } from '../../api/types';
import { ShapeGlyph } from '../ui';
import { TIER_BANDS, TIER_STYLE, tierLabel } from './beatStyle';

/**
 * Linear 0–100 gauge with the tier bands. The tier shown is always the one the API computed; the marker
 * position is the score. Tier is carried by name, shape and position — not colour alone.
 */
export function RiskGauge({ risk }: { risk: Risk | null }) {
  const score = risk ? Math.max(0, Math.min(100, risk.score)) : null;
  const st = risk ? TIER_STYLE[risk.tier] : null;
  return (
    <div>
      <div className="flex items-end justify-between gap-3">
        <div className="flex items-baseline gap-2">
          <span className="num text-4xl font-semibold leading-none" style={{ color: st?.text ?? '#7F93A6' }}>
            {score === null ? '—' : Math.round(score)}
          </span>
          <span className="text-xs text-fg-faint">/ 100</span>
        </div>
        {risk && st ? (
          <div className="text-right">
            <div className="inline-flex items-center gap-1.5 text-sm font-semibold" style={{ color: st.text }}>
              <ShapeGlyph shape={st.shape} color={st.color} size={13} />
              {tierLabel(risk.tier)}
            </div>
            <div className="text-2xs text-fg-faint">{st.action}</div>
          </div>
        ) : (
          <div className="text-xs text-fg-faint">Waiting for the first rhythm window</div>
        )}
      </div>

      <div
        className="relative mt-3"
        role="meter"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={score ?? undefined}
        aria-valuetext={risk ? `Risk score ${Math.round(risk.score)} of 100, tier ${tierLabel(risk.tier)}` : 'No risk score yet'}
        aria-label="Risk score"
      >
        <div className="flex h-2.5 gap-[2px]">
          {TIER_BANDS.map((b) => {
            const on = risk?.tier === b.tier;
            return (
              <div
                key={b.tier}
                className="h-full first:rounded-l-sm last:rounded-r-sm"
                style={{
                  width: `${b.to - b.from}%`,
                  background: TIER_STYLE[b.tier].color,
                  opacity: on ? 1 : 0.32,
                  outline: on ? '1px solid #E6EDF3' : 'none',
                  outlineOffset: 1,
                }}
              />
            );
          })}
        </div>
        {score !== null && (
          <div className="absolute -top-1.5 h-[22px] w-0" style={{ left: `${score}%`, transition: 'left 600ms ease-out' }} aria-hidden="true">
            <div className="absolute -left-[1.5px] top-0 h-[22px] w-[3px] rounded-sm bg-fg" />
          </div>
        )}
        <div className="mt-1.5 flex text-2xs text-fg-faint" aria-hidden="true">
          {TIER_BANDS.map((b) => (
            <div key={b.tier} style={{ width: `${b.to - b.from}%` }} className="flex items-center gap-1">
              <ShapeGlyph shape={TIER_STYLE[b.tier].shape} color={TIER_STYLE[b.tier].color} size={8} />
              <span className={risk?.tier === b.tier ? 'font-semibold text-fg' : ''}>{TIER_STYLE[b.tier].meaning}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="mt-3">
        <div className="label mb-1">Contributing factors</div>
        {risk && risk.factors.length > 0 ? (
          <ul className="space-y-1">
            {risk.factors.map((f, i) => (
              <li key={`${f.rule_id}-${i}`} className="flex items-start justify-between gap-3 text-sm">
                <span className="min-w-0 text-fg">{f.text}</span>
                <span className="num shrink-0 text-xs text-fg-muted" title={`Rule ${f.rule_id}`}>+{f.points}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-fg-muted">{risk ? 'No contributing findings in the current window.' : '—'}</p>
        )}
      </div>
    </div>
  );
}
