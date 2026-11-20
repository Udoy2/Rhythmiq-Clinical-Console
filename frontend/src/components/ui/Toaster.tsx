import { Link } from 'react-router-dom';
import { useToasts } from '../../state/toasts';
import { TIER_STYLE, tierLabel } from '../ecg/beatStyle';
import { Button, ShapeGlyph } from './index';

export function Toaster() {
  const { toasts, dismiss } = useToasts();
  return (
    <div className="pointer-events-none fixed right-3 top-14 z-50 flex w-[22rem] max-w-[calc(100vw-1.5rem)] flex-col gap-2" aria-live="assertive">
      {toasts.map((t) => {
        const tier = t.tier ? TIER_STYLE[t.tier] : null;
        const color = tier?.color ?? (t.tone === 'error' ? '#b42318' : t.tone === 'ok' ? '#1f7a4d' : '#6b7c90');
        return (
          <div
            key={t.id}
            role={t.tone === 'alert' || t.tone === 'error' ? 'alert' : 'status'}
            className="pointer-events-auto rounded-md border bg-white border-line px-3 py-2.5 shadow-sm"
            style={{ borderColor: color, borderLeftWidth: 4 }}
          >
            <div className="flex items-start gap-2">
              <span className="mt-0.5">
                <ShapeGlyph shape={tier?.shape ?? (t.tone === 'error' ? 'octagon' : 'circle')} color={color} size={14} />
              </span>
              <div className="min-w-0 flex-1">
                {t.tier && <div className="text-2xs font-semibold" style={{ color: tier?.text }}>{tierLabel(t.tier)} alert</div>}
                <div className="text-sm font-semibold text-fg">{t.title}</div>
                {t.body && <div className="mt-0.5 text-xs text-fg-muted">{t.body}</div>}
                {t.href && (
                  <Link to={t.href} onClick={() => dismiss(t.id)} className="mt-1 inline-block text-xs font-medium text-teal-text underline underline-offset-2">
                    {t.hrefLabel ?? 'Open'}
                  </Link>
                )}
              </div>
              <Button variant="ghost" size="sm" onClick={() => dismiss(t.id)} aria-label="Dismiss notification">Dismiss</Button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
