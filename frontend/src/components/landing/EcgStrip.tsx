import { BEAT_STYLE } from '../ecg/beatStyle';
import { ShapeGlyph } from '../ui';

// One printed strip: three normal beats and one premature ventricular beat, drawn with the console's own encoding.
const BEAT = (o: number) =>
  `L${o + 40} 110 Q${o + 50} 100 ${o + 60} 110 L${o + 80} 110 L${o + 86} 120 L${o + 94} 44 L${o + 102} 140 L${o + 108} 110 L${o + 130} 110 Q${o + 148} 84 ${o + 166} 110`;
const V_BEAT = (o: number) =>
  `L${o + 70} 110 L${o + 95} 30 L${o + 130} 160 Q${o + 150} 150 ${o + 170} 110`;
const TRACE = `M0 110 ${BEAT(0)} ${BEAT(180)} ${V_BEAT(360)} L540 110 ${BEAT(540)} L720 110`;
const MARKS = [
  { x: 94, y: 28, label: 'N' as const },
  { x: 274, y: 28, label: 'N' as const },
  { x: 455, y: 14, label: 'V' as const },
  { x: 634, y: 28, label: 'N' as const },
];

export function EcgStrip({ className }: { className?: string }) {
  return (
    <figure className={className}>
      <div className="paper-grid overflow-hidden rounded-md border border-line">
        <svg viewBox="0 0 720 200" className="block w-full" role="img" aria-label="Illustrative ECG strip: three normal beats and one premature ventricular beat">
          <path d={TRACE} fill="none" stroke="#17130F" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
          {MARKS.map((m) => {
            const st = BEAT_STYLE[m.label];
            return m.label === 'V'
              ? <rect key={m.x} x={m.x - 6} y={m.y - 6} width="12" height="12" fill={st.color} />
              : <circle key={m.x} cx={m.x} cy={m.y} r="6" fill={st.color} />;
          })}
        </svg>
      </div>
      <figcaption className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fg-muted">
        <span className="inline-flex items-center gap-1.5"><ShapeGlyph shape={BEAT_STYLE.N.shape} color={BEAT_STYLE.N.color} size={11} /> N Normal</span>
        <span className="inline-flex items-center gap-1.5"><ShapeGlyph shape={BEAT_STYLE.V.shape} color={BEAT_STYLE.V.color} size={11} /> V Ventricular ectopic</span>
        <span>Illustration, not patient data</span>
      </figcaption>
    </figure>
  );
}
