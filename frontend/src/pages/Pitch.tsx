import { useCallback, useEffect, useRef, type ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { DISCLAIMER } from '../api/types';
import { BrandLogo } from '../components/BrandLogo';
import { cx } from '../lib/format';

function Kicker({ children }: { children: ReactNode }) {
  return <p className="num text-xs font-semibold uppercase tracking-widest text-teal-text">{children}</p>;
}

function Title({ children }: { children: ReactNode }) {
  return <h2 className="mt-2 max-w-3xl text-3xl font-extrabold leading-tight tracking-tight text-ink sm:text-4xl lg:text-5xl">{children}</h2>;
}

function Tile({ head, children }: { head: ReactNode; children: ReactNode }) {
  return (
    <div className="panel p-5">
      <h3 className="text-base font-bold text-ink">{head}</h3>
      <p className="mt-1 text-sm leading-relaxed text-fg-muted">{children}</p>
    </div>
  );
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="panel p-5">
      <div className="font-display text-3xl font-extrabold sm:text-4xl" style={GRADIENT_TEXT}>{value}</div>
      <div className="mt-1 text-sm text-fg-muted">{label}</div>
    </div>
  );
}

const GRADIENT_TEXT = { backgroundImage: 'var(--rq-gradient)', WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent' } as const;
const BEAT = (o: number) => `L${o + 40} 70 Q${o + 50} 60 ${o + 60} 70 L${o + 80} 70 L${o + 86} 80 L${o + 94} 14 L${o + 102} 100 L${o + 108} 70 L${o + 130} 70 Q${o + 148} 48 ${o + 166} 70`;
const TRACE = `M0 70 ${BEAT(0)} ${BEAT(180)} ${BEAT(360)} ${BEAT(540)} L720 70`;

const PIPELINE = [
  { step: 'ECG stream', note: 'Low-cost sensor or a replayed public recording, over WebSocket' },
  { step: 'Beat detection', note: 'R-peaks found in the live signal, with signal quality tracked' },
  { step: 'Beat classification', note: 'ResNet1D labels each beat N, S, V, F — or Unknown when unsure' },
  { step: 'Rhythm rules', note: 'Deterministic, versioned rules: couplets, burden, runs' },
  { step: 'Risk and alerts', note: 'A tiered score, each alert carrying its evidence strip' },
];

const SLIDES: { name: string; body: ReactNode }[] = [
  {
    name: 'Rhythmiq',
    body: (
      <div className="rounded-[28px] px-6 py-8 text-white sm:px-12 sm:py-9" style={{ background: 'linear-gradient(135deg, #0B1F3A 0%, #12305a 60%, #0d3b52 100%)', boxShadow: 'var(--rq-shadow)' }}>
        <BrandLogo size="lg" withWordmark onDark />
        <h1 className="mt-6 text-4xl font-extrabold leading-tight tracking-tight !text-white sm:text-5xl lg:text-6xl">
          See every beat.<br /><span style={GRADIENT_TEXT}>Act on evidence.</span>
        </h1>
        <p className="mt-4 max-w-xl text-lg leading-relaxed text-white/80">
          Rhythmiq turns a low-cost ECG stream into a live clinical workspace: every beat labelled, every alert backed by the trace that caused it.
        </p>
        <svg viewBox="0 0 720 120" className="mt-5 block w-full max-w-2xl" role="img" aria-label="Illustrative ECG trace">
          <defs><linearGradient id="pitch-trace" x1="0" x2="1"><stop offset="0" stopColor="#00B86B" /><stop offset="1" stopColor="#22D3EE" /></linearGradient></defs>
          <path d={TRACE} fill="none" stroke="url(#pitch-trace)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </div>
    ),
  },
  {
    name: 'The problem',
    body: (
      <div>
        <Kicker>The problem</Kicker>
        <Title>A continuous ECG is hours of trace. The beats that matter are seconds of it.</Title>
        <div className="mt-8 grid gap-3 md:grid-cols-3">
          <Tile head="Too much signal to watch">Abnormal beats are intermittent. Nobody can sit and read a live strip beat by beat for a whole session.</Tile>
          <Tile head="Monitoring is expensive">Continuous rhythm monitoring usually means dedicated equipment, which keeps it out of low-resource settings.</Tile>
          <Tile head="AI without evidence is hard to trust">A score with no trace behind it gives a clinician nothing to check. Confident guesses on noisy signal make it worse.</Tile>
        </div>
      </div>
    ),
  },
  {
    name: 'How it works',
    body: (
      <div>
        <Kicker>How it works</Kicker>
        <Title>One pipeline, from electrode to alert.</Title>
        <ol className="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {PIPELINE.map((p, i) => (
            <li key={p.step} className="panel p-5">
              <div className="num text-xs font-semibold text-teal-text">{String(i + 1).padStart(2, '0')}</div>
              <h3 className="mt-1 text-base font-bold text-ink">{p.step}</h3>
              <p className="mt-1 text-sm leading-relaxed text-fg-muted">{p.note}</p>
            </li>
          ))}
        </ol>
        <p className="mt-6 max-w-3xl text-base leading-relaxed text-fg-muted">
          The model labels beats. The rules decide what a pattern of beats means. Keeping those two apart is what makes every alert explainable.
        </p>
      </div>
    ),
  },
  {
    name: 'The console',
    body: (
      <div>
        <Kicker>What we built</Kicker>
        <Title>A working, signed-in console — not a mock-up.</Title>
        <div className="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Tile head="Live console">The streaming trace with each beat marked by shape, pattern and colour, beside current rhythm and risk.</Tile>
          <Tile head="Alerts with evidence">A triage queue, critical first. Each alert opens on the strip and the rule that raised it.</Tile>
          <Tile head="History and trends">Stored sessions, heart-rate and burden trends, and a beat-by-beat review of any moment.</Tile>
          <Tile head="Cited AI insight">A copilot that answers from this patient's records, cites each claim, and never gives a diagnosis.</Tile>
          <Tile head="Reports">A printable session summary a clinician can file or hand over.</Tile>
          <Tile head="Roles and audit">Clinician, admin, technician and patient views, device pairing, and an audit log.</Tile>
        </div>
      </div>
    ),
  },
  {
    name: 'Honest numbers',
    body: (
      <div>
        <Kicker>Model and honesty</Kicker>
        <Title>Tested on patients the model has never seen.</Title>
        <div className="mt-8 grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat value="97.9%" label="of beats detected end to end" />
          <Stat value="88.3%" label="beat-label accuracy end to end" />
          <Stat value="84.8%" label="ventricular beat sensitivity" />
          <Stat value="6.0%" label="of beats marked Unknown, not guessed" />
        </div>
        <p className="mt-6 max-w-3xl text-base leading-relaxed text-fg-muted">
          Measured on MIT-BIH DS2 — 22 patients held out of training — with the stand-in model that ships in this repository.
          Supraventricular and fusion beats are still weak (17.7% and 16.8% sensitivity), and we say so in the product.
        </p>
        <p className="mt-2 max-w-3xl text-sm text-fg-faint">{DISCLAIMER}</p>
      </div>
    ),
  },
  {
    name: 'Status and next',
    body: (
      <div>
        <Kicker>Where we are</Kicker>
        <Title>Built end to end. Next: real hardware and a stronger model.</Title>
        <div className="mt-8 grid gap-3 md:grid-cols-3">
          <Tile head="Working today">React console, FastAPI backend, PostgreSQL, live WebSocket feed, beat model, rules engine, alerts, history, insight and reports.</Tile>
          <Tile head="Next">Swap in the larger model trained on MIT-BIH and INCART, and validate the AD8232 sensor path on a bench.</Tile>
          <Tile head="Not done yet">The sensor firmware is written but not flashed. Password reset and MFA enrolment are not built.</Tile>
        </div>
        <div className="mt-8 flex flex-wrap items-center gap-3">
          <Link to="/login" className="btn-primary">Open the live demo</Link>
          <Link to="/" className="btn-secondary">Rhythmiq home</Link>
        </div>
      </div>
    ),
  },
];

export default function PitchPage() {
  const [params, setParams] = useSearchParams();
  const last = SLIDES.length - 1;
  const raw = Number(params.get('s'));
  const index = Number.isInteger(raw) && raw >= 1 && raw <= SLIDES.length ? raw - 1 : 0;
  const go = useCallback((i: number) => {
    const next = Math.max(0, Math.min(last, i));
    setParams(next === 0 ? {} : { s: String(next + 1) }, { replace: true });
  }, [last, setParams]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight' || e.key === 'PageDown' || e.key === ' ') { e.preventDefault(); go(index + 1); }
      else if (e.key === 'ArrowLeft' || e.key === 'PageUp') { e.preventDefault(); go(index - 1); }
      else if (e.key === 'Home') go(0);
      else if (e.key === 'End') go(last);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [go, index, last]);

  const touch = useRef<{ x: number; y: number } | null>(null);
  const stage = useRef<HTMLDivElement>(null);
  useEffect(() => { stage.current?.scrollTo({ top: 0 }); }, [index]);

  const slide = SLIDES[index];
  const arrow = 'inline-flex h-11 w-11 items-center justify-center rounded-full border border-line bg-white text-ink transition-colors hover:bg-ink-800 disabled:cursor-default disabled:opacity-35';

  return (
    <div className="flex h-[100dvh] flex-col bg-ink-950 text-ink">
      <header className="flex items-center gap-3 border-b border-line px-4 py-3 sm:px-6">
        <Link to="/" aria-label="Rhythmiq home"><BrandLogo size="sm" withWordmark /></Link>
        <span className="hidden text-sm text-fg-muted sm:inline">Pitch deck</span>
        <Link to="/login" className="btn-secondary ml-auto">Sign in</Link>
      </header>

      <main
        ref={stage}
        className="flex-1 overflow-y-auto"
        onTouchStart={(e) => { touch.current = { x: e.touches[0].clientX, y: e.touches[0].clientY }; }}
        onTouchEnd={(e) => {
          const s = touch.current; touch.current = null;
          if (!s) return;
          const dx = e.changedTouches[0].clientX - s.x;
          const dy = e.changedTouches[0].clientY - s.y;
          if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) go(index + (dx < 0 ? 1 : -1));
        }}
      >
        <section
          key={index}
          aria-roledescription="slide"
          aria-label={`Slide ${index + 1} of ${SLIDES.length}: ${slide.name}`}
          className="pitch-slide mx-auto flex min-h-full w-full max-w-6xl flex-col justify-center px-4 py-8 sm:px-10 sm:py-12"
        >
          {slide.body}
        </section>
      </main>

      <footer className="flex items-center gap-3 border-t border-line bg-white px-4 py-3 sm:px-6">
        <button type="button" className={arrow} onClick={() => go(index - 1)} disabled={index === 0} aria-label="Previous slide">
          <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true"><path d="M11 3 5 9l6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </button>
        <div className="flex min-w-0 flex-1 items-center justify-center gap-3">
          <div className="flex items-center gap-1.5">
            {SLIDES.map((s, i) => (
              <button
                key={s.name}
                type="button"
                onClick={() => go(i)}
                aria-label={`Go to slide ${i + 1}: ${s.name}`}
                aria-current={i === index ? 'true' : undefined}
                className="flex h-6 items-center"
              >
                <span className={cx('block h-1.5 rounded-full transition-all', i === index ? 'w-6 bg-navy' : 'w-1.5 bg-line')} />
              </button>
            ))}
          </div>
          <span className="num text-xs text-fg-muted" aria-live="polite">{index + 1} / {SLIDES.length}</span>
          <span className="hidden truncate text-xs font-semibold text-fg-muted sm:inline">{slide.name}</span>
        </div>
        <button type="button" className={arrow} onClick={() => go(index + 1)} disabled={index === last} aria-label="Next slide">
          <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true"><path d="m7 3 6 6-6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </button>
      </footer>
    </div>
  );
}
