import { cx } from '../lib/format';

type Size = 'sm' | 'md' | 'lg' | 'hero';

/** Official Rhythmiq mark from the website brand kit. */
export function BrandLogo({
  size = 'md',
  withWordmark = false,
  className,
}: {
  size?: Size;
  withWordmark?: boolean;
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
          <span className={'block text-[1.05rem] font-extrabold tracking-[0.08em] text-ink'}>
            RHYTHMIQ
          </span>
          <span className={'block text-xs font-bold tracking-[0.2em] text-accent-text'}>
            SINCE 2026
          </span>
        </span>
      )}
      {!withWordmark && <span className="sr-only">Rhythmiq</span>}
    </span>
  );
}
