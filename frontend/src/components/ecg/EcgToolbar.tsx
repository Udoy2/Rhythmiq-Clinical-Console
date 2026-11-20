import type { ReactNode } from 'react';
import { GAINS_MM_MV, SPEEDS_MM_S } from '../../live/scale';
import { Segmented, Toggle } from '../ui';

export interface EcgViewSettings { speedMmS: number; gainMmMv: number; showTruth: boolean }

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
      <div className="flex items-center gap-1.5">
        <span className="label">Speed</span>
        <Segmented label="Sweep speed in millimetres per second" value={settings.speedMmS} options={SPEEDS_MM_S}
          onChange={(v) => onChange({ ...settings, speedMmS: v })} />
        <span className="text-2xs text-fg-faint">mm/s</span>
      </div>
      <div className="flex items-center gap-1.5">
        <span className="label">Gain</span>
        <Segmented label="Gain in millimetres per millivolt" value={settings.gainMmMv} options={GAINS_MM_MV}
          onChange={(v) => onChange({ ...settings, gainMmMv: v })} />
        <span className="text-2xs text-fg-faint">mm/mV</span>
      </div>
      <span title={truthAvailable ? 'Show the cardiologist labels of the replayed recording under the model labels' : 'Ground truth exists only for labelled replay recordings'}>
        <Toggle label="Ground truth" checked={settings.showTruth && truthAvailable} disabled={!truthAvailable}
          onChange={(v) => onChange({ ...settings, showTruth: v })} />
      </span>
      {right && <div className="ml-auto flex items-center gap-2">{right}</div>}
    </div>
  );
}
