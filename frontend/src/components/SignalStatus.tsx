import type { SignalStatus } from '../api/types';
import { ShapeGlyph } from './ui';

export function SignalStatusBadge({ status }: { status: SignalStatus | null | undefined }) {
  const map = {
    streaming: { text: 'Streaming', color: '#00B86B', shape: 'circle' as const, cls: 'bg-teal-dim text-teal-text border-teal/40' },
    lead_off: { text: 'Lead-off', color: '#d97706', shape: 'triangle' as const, cls: 'bg-amber-dim text-amber-text border-amber/50' },
    offline: { text: 'Offline', color: '#64748B', shape: 'ring' as const, cls: 'bg-ink-800 text-fg-muted border-line' },
  };
  const s = map[(status ?? 'offline') as keyof typeof map] ?? { text: String(status), color: '#64748B', shape: 'ring' as const, cls: 'bg-ink-800 text-fg-muted border-line' };
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium ${s.cls}`}>
      <ShapeGlyph shape={s.shape} color={s.color} size={10} />
      {s.text}
    </span>
  );
}
