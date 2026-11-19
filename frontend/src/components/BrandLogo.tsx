import { cx } from '../lib/format';

type Size = 'sm' | 'md' | 'lg' | 'hero';

/** Official Rhythmiq mark from the website brand kit. */
export function BrandLogo({
  size = 'md',
  withWordmark = false,
  onDark = false,
  className,
}: {
  size?: Size;
  withWordmark?: boolean;
  onDark?: boolean;
  className?: string;
}) {
  const box = { sm: 'h-9 w-9', md: 'h-11 w-11', lg: 'h-14 w-14', hero: 'h-20 w-20 sm:h-24 sm:w-24' }[size];

  return (
    <span className={cx('inline-flex items-center gap-3', className)}>
      <img
        src="/brand/logo/rhythmiq-mark.svg"
        alt=""
        aria-hidden={!withWordmark}
        className={cx('object-contain', box)}
        draggable={false}
      />
      {withWordmark && (
        <span className="leading-tight">
          <span className={cx('block text-[1.05rem] font-extrabold tracking-[0.08em]', onDark ? 'text-white' : 'text-navy')} style={{ fontFamily: 'Manrope, sans-serif' }}>
            RHYTHMIQ
          </span>
          <span className={cx('block text-[0.62rem] font-bold tracking-[0.35em]', onDark ? 'text-[var(--rq-green-bright)]' : 'text-teal')}>
            SINCE 2026
          </span>
        </span>
      )}
      {!withWordmark && <span className="sr-only">Rhythmiq</span>}
    </span>
  );
}
