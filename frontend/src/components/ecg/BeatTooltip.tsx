import type { BeatLabel } from '../../api/types';
import { agreement, type TimedBeat } from '../../live/beatMatch';
import { fmtClock } from '../../live/scale';
import { BEAT_STYLE } from './beatStyle';
import { ShapeGlyph } from '../ui';

const CLASSES: ('N' | 'S' | 'V' | 'F')[] = ['N', 'S', 'V', 'F'];

export function BeatDetails({ beat }: { beat: TimedBeat }) {
  const st = BEAT_STYLE[beat.label] ?? BEAT_STYLE.Unknown;
  const ag = agreement(beat);
  const truth = beat.truth ? BEAT_STYLE[beat.truth as BeatLabel] : null;
  return (
    <>
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1.5 font-semibold" style={{ color: st.text }}>
          <ShapeGlyph shape={st.shape} color={st.color} size={12} />
          <span className="font-mono">{st.letter}</span>
          <span className="font-sans text-fg">{st.name}</span>
        </span>
        <span className="num text-fg-muted">{fmtClock(beat.t)}<span className="text-fg-faint">.{String(Math.floor((beat.t % 1) * 1000)).padStart(3, '0')}</span></span>
      </div>
      <div className="mt-1.5 flex items-baseline justify-between">
        <span className="text-fg-muted">Confidence</span>
        <span className="num font-semibold text-fg">{(beat.confidence * 100).toFixed(1)}%</span>
      </div>
      <table className="mt-1 w-full text-xs" aria-label="Class probabilities">
        <tbody>
          {CLASSES.map((c) => {
            const p = beat.probs[c] ?? 0;
            return (
              <tr key={c}>
                <td className="w-5 border-0 p-0 py-[1px] font-mono font-semibold" style={{ color: BEAT_STYLE[c].text }}>{c}</td>
                <td className="border-0 p-0 py-[1px]">
                  <div className="h-1.5 w-full rounded-sm bg-paper-edge">
                    <div className="h-1.5 rounded-sm" style={{ width: `${Math.max(0, Math.min(1, p)) * 100}%`, background: BEAT_STYLE[c].color }} />
                  </div>
                </td>
                <td className="w-12 border-0 p-0 py-[1px] text-right num text-fg-muted">{p.toFixed(3)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {beat.label === 'Unknown' && <p className="mt-1 text-fg-faint">Below the confidence threshold — shown as Unknown.</p>}
      <div className="mt-1.5 flex items-baseline justify-between gap-2 border-t border-line pt-1.5">
        <span className="text-fg-muted">Ground truth</span>
        {truth ? (
          <span className="inline-flex items-center gap-1.5">
            <span className="font-mono font-semibold" style={{ color: truth.text }}>{truth.letter}</span>
            <span className={ag === 'mismatch' ? 'font-semibold text-alert-text' : 'text-fg-muted'}>
              {ag === 'mismatch' ? '≠ differs from model' : '= agrees'}
            </span>
          </span>
        ) : (
          <span className="text-fg-faint">none (not a labelled replay)</span>
        )}
      </div>
      <div className="mt-1 flex items-baseline justify-between gap-2">
        <span className="shrink-0 text-fg-muted">Model</span>
        <span className="truncate font-mono text-2xs text-fg-muted" title={beat.model_version}>{beat.model_version || '—'}</span>
      </div>
    </>
  );
}

export function BeatTooltip({ beat, pinned }: { beat: TimedBeat; pinned?: boolean }) {
  return (
    <div role="tooltip" className="rounded border border-line bg-paper-raised p-2 text-xs shadow-md">
      <BeatDetails beat={beat} />
      <div className="mt-1 text-2xs text-fg-faint">{pinned ? 'Pinned — click again or press Esc to release' : 'Click to pin'}</div>
      <span className="sr-only" aria-live="polite">
        Beat {beat.label}, confidence {(beat.confidence * 100).toFixed(0)} percent{beat.truth ? `, ground truth ${beat.truth}` : ''}
      </span>
    </div>
  );
}
