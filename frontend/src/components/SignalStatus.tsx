import type { SignalStatus } from '../api/types';
import { ShapeGlyph } from './ui';

export function SignalStatusBadge({ status }: { status: SignalStatus | null | undefined }) {
  const map = {
    streaming: { text: 'Streaming', color: '#00A896', shape: 'circle' as const, cls: 'text-teal-text' },
    lead_off: { text: 'Lead-off', color: '#C9820A', shape: 'triangle' as const, cls: 'text-amber-text' },
    offline: { text: 'Offline', color: '#7F93A6', shape: 'ring' as const, cls: 'text-fg-muted' },
  };
  const s = map[(status ?? 'offline') as keyof typeof map] ?? { text: String(status), color: '#7F93A6', shape: 'ring' as const, cls: 'text-fg-muted' };
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap text-xs font-medium ${s.cls}`}>
      <ShapeGlyph shape={s.shape} color={s.color} size={10} />
      {s.text}
    </span>
  );
}
