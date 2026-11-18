import {
  forwardRef, useEffect, useId, useRef,
  type ButtonHTMLAttributes, type ReactNode, type SelectHTMLAttributes,
} from 'react';
import type { UseQueryResult } from '@tanstack/react-query';
import { errorText } from '../../api/client';
import type { BeatLabel, Tier } from '../../api/types';
import { cx } from '../../lib/format';
import { BEAT_STYLE, beatCssBackground, shapeSvgPath, TIER_STYLE, type Shape } from '../ecg/beatStyle';

// ───────────── Button ─────────────
type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';
interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> { variant?: Variant; size?: 'sm' | 'md'; busy?: boolean }
const VARIANT: Record<Variant, string> = {
  primary: 'bg-teal text-ink-950 hover:bg-[#19BBA9] border-transparent font-semibold',
  secondary: 'bg-ink-700 text-fg hover:bg-ink-600 border-line',
  ghost: 'bg-transparent text-fg-muted hover:text-fg hover:bg-ink-700 border-transparent',
  danger: 'bg-coral-strong text-white hover:bg-[#D23F44] border-transparent font-semibold',
};
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', busy, className, children, disabled, type = 'button', ...rest }, ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={cx(
        'inline-flex items-center justify-center gap-1.5 rounded border whitespace-nowrap transition-colors',
        'disabled:opacity-50 disabled:cursor-not-allowed',
        size === 'sm' ? 'h-7 px-2 text-xs' : 'h-8 px-3 text-sm',
        VARIANT[variant], className,
      )}
      {...rest}
    >
      {busy && <Spinner size={12} />}
      {children}
    </button>
  );
});

// ───────────── Spinner / skeleton ─────────────
export function Spinner({ size = 16, label }: { size?: number; label?: string }) {
  return (
    <span role="status" className="inline-flex items-center gap-2 text-fg-muted text-sm">
      <svg width={size} height={size} viewBox="0 0 16 16" className="animate-spin" aria-hidden="true">
        <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeOpacity="0.25" strokeWidth="2" />
        <path d="M8 2a6 6 0 0 1 6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      </svg>
      {label ? <span>{label}</span> : <span className="sr-only">Loading</span>}
    </span>
  );
}
export function Skeleton({ className }: { className?: string }) {
  return <div className={cx('animate-pulse rounded bg-ink-700/70', className)} aria-hidden="true" />;
}

// ───────────── Shape glyph ─────────────
export function ShapeGlyph({ shape, color, size = 12, hollow }: { shape: Shape; color: string; size?: number; hollow?: boolean }) {
  const open = hollow || shape === 'ring';
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" className="shrink-0">
      <path d={shapeSvgPath(shape)} fill={open ? 'none' : color} stroke={color} strokeWidth={open ? 2 : 0} />
    </svg>
  );
}

// ───────────── Tier badge ─────────────
export function TierBadge({ tier, score, compact }: { tier: Tier | null | undefined; score?: number | null; compact?: boolean }) {
  if (!tier || !TIER_STYLE[tier]) {
    return <span className="inline-flex items-center gap-1.5 rounded border border-line px-1.5 py-0.5 text-xs text-fg-faint">No data</span>;
  }
  const st = TIER_STYLE[tier];
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded border px-1.5 py-0.5 text-xs font-medium whitespace-nowrap"
      style={{ borderColor: st.color, background: st.bg, color: st.text }}
    >
      <ShapeGlyph shape={st.shape} color={st.color} size={11} />
      <span>{compact ? st.meaning : `${st.name} — ${st.meaning}`}</span>
      {typeof score === 'number' && <span className="num text-fg border-l pl-1.5" style={{ borderColor: `${st.color}80` }}>{Math.round(score)}</span>}
    </span>
  );
}

// ───────────── Beat class chip + legend ─────────────
export function BeatChip({ label, withName }: { label: BeatLabel; withName?: boolean }) {
  const st = BEAT_STYLE[label] ?? BEAT_STYLE.Unknown;
  return (
    <span className="inline-flex items-center gap-1 text-xs whitespace-nowrap" style={{ color: st.text }}>
      <ShapeGlyph shape={st.shape} color={st.color} size={11} />
      <span className="font-mono font-semibold">{st.letter}</span>
      {withName && <span className="text-fg-muted font-sans">{st.name}</span>}
    </span>
  );
}
export function BeatSwatch({ label }: { label: BeatLabel }) {
  const st = BEAT_STYLE[label];
  return (
    <span
      aria-hidden="true"
      className="inline-block h-3 w-3 shrink-0 rounded-[2px]"
      style={{ background: beatCssBackground(label), border: `1px solid ${st.color}` }}
    />
  );
}
export function BeatLegend({ className }: { className?: string }) {
  return (
    <ul className={cx('flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fg-muted', className)} aria-label="Beat class legend">
      {(Object.keys(BEAT_STYLE) as BeatLabel[]).map((l) => (
        <li key={l} className="inline-flex items-center gap-1.5">
          <ShapeGlyph shape={BEAT_STYLE[l].shape} color={BEAT_STYLE[l].color} size={11} />
          <BeatSwatch label={l} />
          <span><span className="font-mono font-semibold" style={{ color: BEAT_STYLE[l].text }}>{BEAT_STYLE[l].letter}</span> {BEAT_STYLE[l].name}</span>
        </li>
      ))}
    </ul>
  );
}

// ───────────── Badge ─────────────
export function Badge({ children, tone = 'neutral', className, title }: {
  children: ReactNode; tone?: 'neutral' | 'teal' | 'amber' | 'coral' | 'outline'; className?: string; title?: string;
}) {
  const tones = {
    neutral: 'bg-ink-700 text-fg-muted border-line',
    teal: 'bg-teal-dim/60 text-teal-text border-teal/50',
    amber: 'bg-amber-dim text-amber-text border-amber/60',
    coral: 'bg-coral-dim text-coral-text border-coral/60',
    outline: 'bg-transparent text-fg-muted border-line',
  } as const;
  return (
    <span title={title} className={cx('inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-2xs font-semibold tracking-wide whitespace-nowrap', tones[tone], className)}>
      {children}
    </span>
  );
}
export function DemoBadge({ className }: { className?: string }) {
  return <Badge tone="amber" className={className} title="Demonstration patient — replayed public recordings, never real patient data">DEMO</Badge>;
}

// ───────────── Banner ─────────────
type Tone = 'info' | 'warn' | 'critical' | 'ok';
const BANNER: Record<Tone, { cls: string; shape: Shape; color: string }> = {
  info: { cls: 'border-line bg-ink-800 text-fg', shape: 'circle', color: '#A9B8C6' },
  ok: { cls: 'border-teal/50 bg-teal-dim/40 text-fg', shape: 'circle', color: '#00A896' },
  warn: { cls: 'border-amber/70 bg-amber-dim text-fg', shape: 'triangle', color: '#C9820A' },
  critical: { cls: 'border-coral/70 bg-coral-dim text-fg', shape: 'octagon', color: '#E0484D' },
};
export function Banner({ tone = 'info', title, children, action, live }: {
  tone?: Tone; title: ReactNode; children?: ReactNode; action?: ReactNode; live?: boolean;
}) {
  const b = BANNER[tone];
  return (
    <div
      role={tone === 'critical' || tone === 'warn' ? 'alert' : 'status'}
      aria-live={live ? 'polite' : undefined}
      className={cx('flex items-start gap-2.5 rounded-md border px-3 py-2 text-sm', b.cls)}
    >
      <span className="mt-0.5"><ShapeGlyph shape={b.shape} color={b.color} size={14} /></span>
      <div className="min-w-0 flex-1">
        <span className="font-semibold">{title}</span>
        {children && <span className="text-fg-muted"> {children}</span>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

// ───────────── Card ─────────────
export function Card({ title, right, children, className, bodyClass, as: As = 'section' }: {
  title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string; bodyClass?: string; as?: 'section' | 'div' | 'aside';
}) {
  return (
    <As className={cx('panel min-w-0', className)}>
      {(title || right) && (
        <div className="panel-head">
          {typeof title === 'string' ? <h2>{title}</h2> : title}
          {right && <div className="flex items-center gap-2">{right}</div>}
        </div>
      )}
      <div className={bodyClass ?? 'p-3'}>{children}</div>
    </As>
  );
}

// ───────────── States ─────────────
export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1.5 px-4 py-8 text-center">
      <p className="text-sm font-medium text-fg">{title}</p>
      {children && <p className="max-w-md text-sm text-fg-muted">{children}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
export function ErrorState({ error, onRetry, title = 'Could not load this' }: { error: unknown; onRetry?: () => void; title?: string }) {
  return (
    <div role="alert" className="flex flex-col items-center justify-center gap-1.5 px-4 py-8 text-center">
      <p className="text-sm font-medium text-coral-text">{title}</p>
      <p className="max-w-md text-sm text-fg-muted">{errorText(error)}</p>
      {onRetry && <Button size="sm" className="mt-2" onClick={onRetry}>Try again</Button>}
    </div>
  );
}
export function LoadingState({ label = 'Loading…' }: { label?: string }) {
  return <div className="flex items-center justify-center px-4 py-8"><Spinner label={label} /></div>;
}

/** Loading / error / empty wrapper for a TanStack query. */
export function QueryState<T>({ query, children, empty, isEmpty, loadingLabel }: {
  query: UseQueryResult<T>;
  children: (data: T) => ReactNode;
  empty?: ReactNode;
  isEmpty?: (data: T) => boolean;
  loadingLabel?: string;
}) {
  if (query.isPending) return <LoadingState label={loadingLabel} />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  const data = query.data as T;
  const none = isEmpty ? isEmpty(data) : Array.isArray(data) && data.length === 0;
  if (none && empty) return <>{empty}</>;
  return <>{children(data)}</>;
}

// ───────────── Form bits ─────────────
export function Field({ label, hint, error, children, className }: {
  label: string; hint?: ReactNode; error?: string | null; children: (id: string, describedBy?: string) => ReactNode; className?: string;
}) {
  const id = useId();
  const descId = `${id}-d`;
  return (
    <div className={cx('flex flex-col gap-1', className)}>
      <label htmlFor={id} className="label">{label}</label>
      {children(id, hint || error ? descId : undefined)}
      {(error || hint) && <p id={descId} className={cx('text-xs', error ? 'text-coral-text' : 'text-fg-faint')}>{error ?? hint}</p>}
    </div>
  );
}
export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select({ className, ...rest }, ref) {
  return <select ref={ref} className={cx('input h-8 py-0 pr-7', className)} {...rest} />;
});

/** Radio-group segmented control. */
export function Segmented<T extends string | number>({ label, value, options, onChange, format, size = 'sm' }: {
  label: string; value: T; options: readonly T[]; onChange: (v: T) => void; format?: (v: T) => ReactNode; size?: 'sm' | 'md';
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded border border-line bg-ink-900 p-0.5">
      {options.map((o) => {
        const on = o === value;
        return (
          <button
            key={String(o)}
            type="button"
            role="radio"
            aria-checked={on}
            onClick={() => onChange(o)}
            className={cx(
              'rounded-[3px] px-2 num whitespace-nowrap transition-colors',
              size === 'sm' ? 'h-6 text-xs' : 'h-7 text-sm',
              on ? 'bg-ink-600 text-fg font-semibold shadow-[inset_0_-2px_0_#00A896]' : 'text-fg-muted hover:text-fg',
            )}
          >
            {format ? format(o) : String(o)}
          </button>
        );
      })}
    </div>
  );
}

export function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="inline-flex items-center gap-2 text-xs text-fg-muted hover:text-fg disabled:opacity-50 disabled:cursor-not-allowed"
    >
      <span className={cx('relative inline-block h-4 w-7 rounded-full border transition-colors', checked ? 'bg-teal border-teal' : 'bg-ink-700 border-line')}>
        <span className={cx('absolute top-[1px] h-3 w-3 rounded-full transition-all', checked ? 'left-[13px] bg-ink-950' : 'left-[1px] bg-fg-muted')} />
      </span>
      <span>{label}</span>
      <span className="sr-only">{checked ? 'on' : 'off'}</span>
    </button>
  );
}

// ───────────── Dialog (native <dialog>: focus trap, Esc, inert background) ─────────────
export function Dialog({ open, onClose, title, children, footer, wide }: {
  open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode; wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onCancel={(e) => { e.preventDefault(); onClose(); }}
      onClick={(e) => { if (e.target === ref.current) onClose(); }}
      className={cx('m-auto w-[calc(100%-2rem)] rounded-md border border-line bg-ink-800 p-0 text-fg backdrop:bg-black/60', wide ? 'max-w-2xl' : 'max-w-md')}
    >
      {open && (
        <div className="flex max-h-[85vh] flex-col">
          <div className="flex items-center justify-between border-b border-line px-4 py-3">
            <h2 id={titleId} className="text-base">{title}</h2>
            <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close dialog">Close</Button>
          </div>
          <div className="overflow-y-auto px-4 py-3">{children}</div>
          {footer && <div className="flex justify-end gap-2 border-t border-line px-4 py-3">{footer}</div>}
        </div>
      )}
    </dialog>
  );
}

// ───────────── Small text helpers ─────────────
export function Stat({ label, value, unit, sub, tone }: { label: string; value: ReactNode; unit?: string; sub?: ReactNode; tone?: string }) {
  return (
    <div className="min-w-0">
      <div className="label truncate">{label}</div>
      <div className="flex items-baseline gap-1">
        <span className="num text-xl font-semibold leading-7" style={tone ? { color: tone } : undefined}>{value}</span>
        {unit && <span className="text-xs text-fg-muted">{unit}</span>}
      </div>
      {sub && <div className="text-2xs text-fg-faint truncate">{sub}</div>}
    </div>
  );
}
export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cx('font-mono text-xs text-fg-muted break-all', className)}>{children}</span>;
}
export function PageHeader({ title, sub, right }: { title: ReactNode; sub?: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="flex flex-wrap items-center gap-2">{title}</h1>
        {sub && <p className="mt-0.5 text-sm text-fg-muted">{sub}</p>}
      </div>
      {right && <div className="flex flex-wrap items-center gap-2">{right}</div>}
    </div>
  );
}
