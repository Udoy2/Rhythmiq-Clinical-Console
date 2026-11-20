import { useCallback, useMemo, useRef, useState } from 'react';
import { normBeat } from '../../api/normalize';
import type { AlertEvidence } from '../../api/types';
import { BeatStore, type TimedBeat } from '../../live/beatMatch';
import type { EcgSource } from '../../live/liveFeed';
import { SampleRing } from '../../live/ringBuffer';
import { fmtClock } from '../../live/scale';
import { BEAT_STYLE } from './beatStyle';
import { EcgCanvas } from './EcgCanvas';
import { EcgToolbar, type EcgViewSettings } from './EcgToolbar';
import { BeatChip, BeatLegend } from '../ui';

/**
 * The stored evidence snapshot of an alert, drawn with the same renderer as the live sweep.
 * Assumes `strip_mv[0]` is at `start_s` and beats carry absolute `time_s` (session time), as on the live socket.
 */
export function EvidenceStrip({ evidence }: { evidence: AlertEvidence }) {
  const fs = evidence.fs > 0 ? evidence.fs : 250;
  const start = evidence.start_s ?? 0;
  const dur = Math.max(evidence.strip_mv?.length ? evidence.strip_mv.length / fs : 0, (evidence.end_s ?? start) - start, 1);
  const end = start + dur;

  const { source, beats } = useMemo(() => {
    const ring = new SampleRing(fs, dur + 1);
    ring.write(Math.round(start * fs), evidence.strip_mv ?? []);
    const store = new BeatStore();
    const list: TimedBeat[] = (evidence.beats ?? []).map((b) => {
      const nb = normBeat(b as unknown as Record<string, unknown>);
      // Beats inside the strip window use absolute session time; tolerate strip-relative times too.
      const t = nb.time_s >= start - 0.5 && nb.time_s <= end + 0.5 ? nb.time_s : nb.time_s + start;
      return { ...nb, t };
    });
    list.forEach((b) => store.add(b));
    const src: EcgSource = { ring, beats: store };
    return { source: src, beats: store.items };
  }, [evidence, fs, dur, start, end]);

  const hasTruth = beats.some((b) => b.truth !== null);
  const [settings, setSettings] = useState<EcgViewSettings>({ speedMmS: 25, gainMmMv: 10, showTruth: true });
  // At 25 mm/s the whole strip fits; at 50 mm/s it can be panned.
  const head = useRef(end);
  const visible = dur * (25 / settings.speedMmS);
  const clamp = useCallback((t: number) => Math.max(Math.min(end, start + visible), Math.min(end, t)), [start, end, visible]);
  const getHead = useCallback(() => clamp(head.current), [clamp]);
  const onPan = useCallback((d: number) => { head.current = clamp(head.current - d); }, [clamp]);

  return (
    <div>
      <EcgToolbar settings={settings} onChange={setSettings} truthAvailable={hasTruth}
        right={<span className="num text-xs text-fg-muted">{fmtClock(start)}–{fmtClock(end)} of the session · {dur.toFixed(0)} s strip</span>} />
      <div className="mt-2">
        <EcgCanvas source={source} getHead={getHead} speedMmS={settings.speedMmS} gainMmMv={settings.gainMmMv} showTruth={settings.showTruth && hasTruth}
          onPan={onPan} refWindowS={dur} ariaLabel="Evidence ECG strip with beat labels" />
      </div>
      <BeatLegend className="mt-2" />
      <details className="mt-3" open>
        <summary className="cursor-pointer text-xs font-medium text-accent-text">Beat labels and probabilities ({beats.length} beats)</summary>
        {beats.length === 0 ? <p className="mt-2 text-sm text-fg-muted">No beats were stored with this snapshot.</p> : (
          <div className="mt-2 max-h-64 overflow-auto rounded border border-line">
            <table>
              <thead>
                <tr>
                  <th scope="col">Time</th><th scope="col">Model label</th><th scope="col" className="text-right">Confidence</th>
                  {(['N', 'S', 'V', 'F'] as const).map((c) => <th key={c} scope="col" className="text-right">P({c})</th>)}
                  {hasTruth && <th scope="col">Ground truth</th>}
                </tr>
              </thead>
              <tbody>
                {beats.map((b) => (
                  <tr key={b.sample}>
                    <td className="num">{b.t.toFixed(2)} s</td>
                    <td><BeatChip label={b.label} withName /></td>
                    <td className="num text-right">{(b.confidence * 100).toFixed(1)}%</td>
                    {(['N', 'S', 'V', 'F'] as const).map((c) => (
                      <td key={c} className="num text-right" style={{ color: b.probs[c] >= 0.5 ? BEAT_STYLE[c].text : undefined }}>{b.probs[c].toFixed(3)}</td>
                    ))}
                    {hasTruth && (
                      <td>
                        {b.truth ? <span className="inline-flex items-center gap-2"><BeatChip label={b.truth} />
                          {b.truth !== b.label && <span className="text-xs font-semibold text-alert-text">≠ differs</span>}</span> : <span className="text-fg-faint">—</span>}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </details>
    </div>
  );
}
