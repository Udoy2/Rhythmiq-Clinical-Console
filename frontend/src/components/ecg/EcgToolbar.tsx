import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { GAINS_MM_MV, SPEEDS_MM_S } from '../../live/scale';
import { Button, Segmented, Toggle } from '../ui';

export interface EcgViewSettings { speedMmS: number; gainMmMv: number; showTruth: boolean }

/** Icon button with a visible tooltip on hover and keyboard focus; `label` is also the accessible name. */
export function IconTipButton({ label, onClick, disabled, children }: { label: string; onClick: () => void; disabled?: boolean; children: ReactNode }) {
  return (
    <span className="group relative inline-flex">
      <Button size="sm" onClick={onClick} disabled={disabled} aria-label={label} className="w-8 !px-0">{children}</Button>
      <span aria-hidden="true" className="pointer-events-none absolute left-1/2 top-full z-20 mt-1 -translate-x-1/2 whitespace-nowrap rounded bg-ink px-2 py-1 text-xs text-white opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
        {label}
      </span>
    </span>
  );
}

const chevron = (d: string) => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
);
export const BackIcon = () => chevron('M10 3 5 8l5 5');
export const ForwardIcon = () => chevron('m6 3 5 5-5 5');

function ViewPopover({ settings, onChange, truthAvailable }: { settings: EcgViewSettings; onChange: (s: EcgViewSettings) => void; truthAvailable: boolean }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); setOpen(false); trigger.current?.focus(); } };
    document.addEventListener('pointerdown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('pointerdown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open]);

  return (
    <div ref={root} className="relative" onBlur={(e) => { const n = e.relatedTarget as Node | null; if (n && !root.current?.contains(n)) setOpen(false); }}>
      <Button ref={trigger} size="sm" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}>
        View
        <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m3 6 5 5 5-5" /></svg>
      </Button>
      {open && (
        <div id={id} role="group" aria-label="View settings" className="absolute right-0 top-full z-20 mt-1 w-64 max-w-[calc(100vw-2rem)] space-y-3 sm:left-0 sm:right-auto rounded border border-line bg-paper-raised p-3 shadow-md">
          <div className="flex items-center justify-between gap-2">
            <span className="label">Speed</span>
            <span className="flex items-center gap-1.5">
              <Segmented label="Sweep speed in millimetres per second" value={settings.speedMmS} options={SPEEDS_MM_S}
                onChange={(v) => onChange({ ...settings, speedMmS: v })} />
              <span className="text-xs text-fg-faint">mm/s</span>
            </span>
          </div>
          <div className="flex items-center justify-between gap-2">
            <span className="label">Gain</span>
            <span className="flex items-center gap-1.5">
              <Segmented label="Gain in millimetres per millivolt" value={settings.gainMmMv} options={GAINS_MM_MV}
                onChange={(v) => onChange({ ...settings, gainMmMv: v })} />
              <span className="text-xs text-fg-faint">mm/mV</span>
            </span>
          </div>
          <span className="block" title={truthAvailable ? 'Show the cardiologist labels of the replayed recording under the model labels' : 'Ground truth exists only for labelled replay recordings'}>
            <Toggle label="Ground truth" checked={settings.showTruth && truthAvailable} disabled={!truthAvailable}
              onChange={(v) => onChange({ ...settings, showTruth: v })} />
          </span>
        </div>
      )}
    </div>
  );
}

/** Playback controls (`left`) sit apart from view settings, which live behind one View popover. */
export function EcgToolbar({ settings, onChange, truthAvailable, left, right }: {
  settings: EcgViewSettings;
  onChange: (s: EcgViewSettings) => void;
  truthAvailable: boolean;
  left?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
      {left}
      <ViewPopover settings={settings} onChange={onChange} truthAvailable={truthAvailable} />
      {right && <div className="ml-auto flex items-center gap-2">{right}</div>}
    </div>
  );
}
