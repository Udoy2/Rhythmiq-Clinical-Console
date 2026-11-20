import { useEffect, useState } from 'react';
import { Link, Navigate } from 'react-router-dom';
import { motion, useReducedMotion } from 'framer-motion';
import { BrandLogo } from '../components/BrandLogo';
import { OrbitalSphere } from '../components/landing/OrbitalSphere';
import { useAuth } from '../state/auth';
import { DISCLAIMER } from '../api/types';
import { cx } from '../lib/format';

const DEMO_PASSWORD = 'CardioSense!23';

const HERO_ECG =
  'M0 180 H180 L210 180 L240 60 L270 260 L300 180 H420 L450 180 L480 100 L510 240 L540 180 H720 L750 180 L780 40 L810 280 L840 180 H960 L990 180 L1020 120 L1050 220 L1080 180 H1200';
const DEMO_ECG =
  'M0 62 H90 L110 62 L130 28 L150 96 L170 62 H260 L280 62 L300 40 L320 84 L340 62 H430 L450 62 L470 18 L490 104 L510 62 H600 L620 62 L640 48 L660 76 L680 62 H720';

const DEMO_MARKERS = [
  { cx: 150, cy: 28, fill: '#FF5F57' },
  { cx: 300, cy: 40, fill: '#00B86B' },
  { cx: 470, cy: 18, fill: '#d97706' },
] as const;

const NAV = [
  { href: '#services', label: 'Capabilities' },
  { href: '#advantage', label: 'Advantage' },
  { href: '#process', label: 'Process' },
  { href: '#demo', label: 'Demo' },
];

const SERVICES = [
  {
    icon: '/brand/icons/heart-pulse.svg',
    title: 'Live console',
    body: 'Stream or replay ECG with sweep, gain, pause, and a beat ribbon that stays readable at a glance.',
    tag: 'Core',
  },
  {
    icon: '/brand/icons/activity.svg',
    title: 'Rhythm & risk',
    body: 'Rules score each window and surface a tier with the findings that contributed — never colour alone.',
    tag: 'Clinical',
  },
  {
    icon: '/brand/icons/shield-check.svg',
    title: 'Alerts centre',
    body: 'When a rule fires, the evidence snapshot stays attached so triage does not start from a blank page.',
    tag: 'Triage',
  },
  {
    icon: '/brand/icons/brain-ai.svg',
    title: 'Grounded insight',
    body: 'Ask what changed. Answers stay cited to retrieved patient context and never invent a diagnosis.',
    tag: 'AI',
  },
];

const ADVANTAGES = [
  {
    icon: '/brand/icons/stethoscope.svg',
    title: 'Human in the loop',
    body: 'Designed for clinicians and demo presenters who need to see the signal, the label, and the reason together.',
  },
  {
    icon: '/brand/icons/shield-check.svg',
    title: 'Honest uncertainty',
    body: 'Low-confidence beats stay Unknown. Risk and alerts carry evidence instead of theatrical colour alone.',
  },
  {
    icon: '/brand/icons/brain-ai.svg',
    title: 'Cited AI only',
    body: 'The copilot answers from retrieved records. It will not prescribe, diagnose, or invent clinical claims.',
  },
  {
    icon: '/brand/icons/calendar.svg',
    title: 'Demo-ready path',
    body: 'Public MIT-BIH replays and role-based accounts let you walk the full loop without real patient data.',
  },
];

const STEPS = [
  { title: 'Sign in', body: 'Pick a demo role and open the monitoring console with a single account.' },
  { title: 'Open a replay', body: 'Choose a DEMO patient and start the labelled MIT-BIH stream in Live console.' },
  { title: 'Watch the loop', body: 'Follow beat labels, risk findings, and alerts as the strip advances.' },
  { title: 'Ask with context', body: 'Use Insights to ask what changed — answers stay tied to retrieved evidence.' },
];

const DEMO_ACCOUNTS = [
  { email: 'clinician@cardiosense.dev', role: 'Clinician', use: 'Live console, alerts, insights' },
  { email: 'admin@cardiosense.dev', role: 'Org admin', use: 'Users, devices, audit' },
  { email: 'tech@cardiosense.dev', role: 'Technician', use: 'Device pairing and signal health' },
  { email: 'patient@cardiosense.dev', role: 'Patient', use: 'Limited patient-facing view' },
];

const DEMO_PATIENTS = [
  { name: 'Critical replay', record: 'MIT-BIH 208', focus: 'Higher ectopy — watch risk and alerts climb' },
  { name: 'Watch replay', record: 'MIT-BIH 223', focus: 'Mixed rhythm suitable for triage practice' },
  { name: 'Stable replay', record: 'MIT-BIH 100', focus: 'Quieter baseline for a calm walkthrough' },
];

/** Public marketing entry — Pathfinder-like composition, Rhythmiq brand. */
export function LandingPage() {
  const token = useAuth((s) => s.accessToken);
  const reduceMotion = useReducedMotion();
  const [scrolled, setScrolled] = useState(false);
  const [showTop, setShowTop] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  const sweep = reduceMotion
    ? undefined
    : { x: [0, -1200] as number[], transition: { duration: 18, ease: 'linear' as const, repeat: Infinity } };
  const demoSweep = reduceMotion
    ? undefined
    : { x: [0, -720] as number[], transition: { duration: 14, ease: 'linear' as const, repeat: Infinity } };
  const livePulse = reduceMotion
    ? undefined
    : {
        scale: [1, 1.45, 1],
        opacity: [1, 0.45, 1],
        transition: { duration: 1.7, ease: 'easeInOut' as const, repeat: Infinity },
      };
  const markerPulse = reduceMotion
    ? undefined
    : {
        r: [5, 6.5, 5],
        opacity: [0.95, 0.5, 0.95],
        transition: { duration: 1.4, ease: 'easeInOut' as const, repeat: Infinity },
      };

  useEffect(() => {
    const onScroll = () => {
      setScrolled(window.scrollY > 24);
      setShowTop(window.scrollY > 480);
    };
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setMenuOpen(false); };
    const onResize = () => { if (window.innerWidth >= 900) setMenuOpen(false); };
    document.addEventListener('keydown', onKey);
    window.addEventListener('resize', onResize);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', onResize);
      document.body.style.overflow = prev;
    };
  }, [menuOpen]);

  if (token) return <Navigate to="/patients" replace />;

  return (
    <div className="landing">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-[60] focus:rounded-full focus:bg-teal focus:px-3 focus:py-1.5 focus:text-navy">
        Skip to content
      </a>

      <header className={cx('landing-nav', scrolled && 'is-scrolled', menuOpen && 'is-open')}>
        <div className="landing-nav-inner">
          <a href="#main" className="landing-nav-brand" onClick={() => setMenuOpen(false)}>
            <BrandLogo size="sm" withWordmark onDark className="landing-nav-logo-sm" />
            <BrandLogo size="md" withWordmark onDark className="landing-nav-logo-md" />
          </a>
          <nav className="landing-nav-links" aria-label="Page">
            {NAV.map((item) => (
              <a key={item.href} href={item.href}>{item.label}</a>
            ))}
          </nav>
          <div className="landing-nav-actions">
            <span className="hidden md:inline-flex">
              <Link to="/pitch" className="landing-btn-ghost landing-nav-cta">Pitch deck</Link>
            </span>
            <Link to="/login" className="landing-btn-ghost landing-nav-cta">
              Sign in
            </Link>
            <button
              type="button"
              className="landing-nav-toggle"
              aria-expanded={menuOpen}
              aria-controls="landing-mobile-nav"
              aria-label={menuOpen ? 'Close menu' : 'Open menu'}
              onClick={() => setMenuOpen((o) => !o)}
            >
              <span className={cx('landing-nav-toggle-bar', menuOpen && 'is-open')} aria-hidden="true" />
            </button>
          </div>
        </div>
        <div
          id="landing-mobile-nav"
          className={cx('landing-nav-drawer', menuOpen && 'is-open')}
          hidden={!menuOpen}
        >
          <nav aria-label="Page sections">
            {NAV.map((item) => (
              <a key={item.href} href={item.href} onClick={() => setMenuOpen(false)}>{item.label}</a>
            ))}
          </nav>
          <Link to="/pitch" className="landing-btn-ghost landing-nav-drawer-cta" onClick={() => setMenuOpen(false)}>
            Pitch deck
          </Link>
          <Link to="/login" className="landing-btn-ghost landing-nav-drawer-cta" onClick={() => setMenuOpen(false)}>
            Sign in
          </Link>
        </div>
      </header>

      <main id="main">
        <section className="landing-hero" aria-labelledby="landing-brand">
          <div className="landing-hero-atmosphere" aria-hidden="true">
            <svg className="landing-ecg" viewBox="0 0 1200 320" preserveAspectRatio="none">
              <defs>
                <linearGradient id="landingEcgGrad" x1="0" y1="0" x2="1200" y2="0" gradientUnits="userSpaceOnUse">
                  <stop offset="0%" stopColor="#00B86B" stopOpacity="0.15" />
                  <stop offset="45%" stopColor="#22D889" stopOpacity="0.9" />
                  <stop offset="100%" stopColor="#22D3EE" stopOpacity="0.35" />
                </linearGradient>
              </defs>
              <motion.g animate={sweep}>
                <path
                  className="landing-ecg-path"
                  d={HERO_ECG}
                  fill="none"
                  stroke="url(#landingEcgGrad)"
                  strokeWidth="3"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                <path
                  className="landing-ecg-path"
                  d={HERO_ECG}
                  transform="translate(1200 0)"
                  fill="none"
                  stroke="url(#landingEcgGrad)"
                  strokeWidth="3"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </motion.g>
            </svg>
          </div>

          <OrbitalSphere />

          <div className="landing-hero-layout">
            <div className="landing-hero-copy">
              <p className="landing-live" aria-hidden="true">
                <motion.span className="landing-live-dot" animate={livePulse} />
                Live monitor
              </p>
              <p id="landing-brand" className="landing-brand-word">Rhythmiq</p>
              <h1 className="landing-headline">
                Your gateway to clearer<br />
                <span className="text-[var(--rq-green-bright)]">rhythm decisions.</span>
              </h1>
              <p className="landing-lede">
                Rhythmiq turns a low-cost ECG stream into a clinician console — with{' '}
                <strong>every beat labelled</strong>, <strong>risk tied to evidence</strong>, and{' '}
                <strong>AI that cites the record</strong>. Research and demonstration, not a medical device.
              </p>
              <div className="landing-actions">
                <Link to="/login" className="btn-primary">
                  Enter the console
                </Link>
                <a href="#services" className="landing-btn-ghost">
                  Explore capabilities
                </a>
              </div>
            </div>
          </div>
        </section>

        <section className="landing-section" id="services" aria-labelledby="services-heading">
          <div className="landing-section-inner landing-center">
            <h2 id="services-heading">Capabilities for the full monitoring loop</h2>
            <p className="landing-section-lede landing-section-lede-center">
              One console for streaming, classification, escalation, and grounded questions — built for demos that stay honest.
            </p>
            <div className="landing-service-grid">
              {SERVICES.map((s) => (
                <article key={s.title} className="landing-service-card">
                  <span className="landing-service-tag">{s.tag}</span>
                  <img src={s.icon} alt="" width={36} height={36} />
                  <h3>{s.title}</h3>
                  <p>{s.body}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="landing-section landing-section-soft" id="advantage" aria-labelledby="advantage-heading">
          <div className="landing-section-inner landing-center">
            <h2 id="advantage-heading">The Rhythmiq advantage</h2>
            <p className="landing-section-lede landing-section-lede-center">
              We go beyond a pretty strip. Care teams need labels, reasons, and limits they can trust in a demo room.
            </p>
            <div className="landing-advantage-grid">
              {ADVANTAGES.map((a) => (
                <article key={a.title} className="landing-advantage-card">
                  <span className="landing-advantage-icon">
                    <img src={a.icon} alt="" width={28} height={28} />
                  </span>
                  <h3>{a.title}</h3>
                  <p>{a.body}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="landing-section" id="process" aria-labelledby="process-heading">
          <div className="landing-section-inner landing-center">
            <h2 id="process-heading">A simple 4-step demo path</h2>
            <p className="landing-section-lede landing-section-lede-center">
              From sign-in to cited insight — guide your audience through the loop without losing the evidence.
            </p>
            <ol className="landing-process">
              {STEPS.map((step, i) => (
                <li key={step.title}>
                  <span className="landing-process-icon" aria-hidden="true">{String(i + 1).padStart(2, '0')}</span>
                  <span className="landing-process-step">Step {i + 1}</span>
                  <h3>{step.title}</h3>
                  <p>{step.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="landing-section landing-section-soft" id="demo" aria-labelledby="demo-heading">
          <div className="landing-section-inner">
            <div className="landing-center">
              <h2 id="demo-heading">Walk the demo in a few minutes</h2>
              <p className="landing-section-lede landing-section-lede-center">
                Mock mode ships with public MIT-BIH replays and role-based accounts. Nothing here is a real patient record.
              </p>
            </div>

            <div className="landing-demo-visual" aria-hidden="true">
              <div className="landing-demo-live">
                <motion.span className="landing-live-dot landing-live-dot-on-light" animate={livePulse} />
                Streaming
              </div>
              <svg viewBox="0 0 720 120" className="landing-demo-ecg-vector">
                <defs>
                  <linearGradient id="demoEcgGrad" x1="0" y1="0" x2="720" y2="0" gradientUnits="userSpaceOnUse">
                    <stop offset="0%" stopColor="#00B86B" stopOpacity="0.2" />
                    <stop offset="50%" stopColor="#00B86B" />
                    <stop offset="100%" stopColor="#22D3EE" stopOpacity="0.35" />
                  </linearGradient>
                </defs>
                <motion.g animate={demoSweep}>
                  <path
                    d={DEMO_ECG}
                    fill="none"
                    stroke="url(#demoEcgGrad)"
                    strokeWidth="3"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                  {DEMO_MARKERS.map((m) => (
                    <motion.circle key={`a-${m.cx}`} cx={m.cx} cy={m.cy} r={5} fill={m.fill} animate={markerPulse} />
                  ))}
                  <path
                    d={DEMO_ECG}
                    transform="translate(720 0)"
                    fill="none"
                    stroke="url(#demoEcgGrad)"
                    strokeWidth="3"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                  {DEMO_MARKERS.map((m) => (
                    <motion.circle key={`b-${m.cx}`} cx={m.cx + 720} cy={m.cy} r={5} fill={m.fill} animate={markerPulse} />
                  ))}
                </motion.g>
              </svg>
            </div>

            <div className="landing-demo-grid">
              <article className="landing-demo-block">
                <div className="landing-demo-head">
                  <span className="landing-demo-icon" aria-hidden="true">
                    <svg viewBox="0 0 64 64" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="32" cy="22" r="10" />
                      <path d="M12 54c4-12 12-18 20-18s16 6 20 18" />
                    </svg>
                  </span>
                  <div>
                    <span className="landing-demo-step">Step 01</span>
                    <h3>Sign in</h3>
                  </div>
                </div>
                <p>Use any demo account with password <span className="landing-mono">{DEMO_PASSWORD}</span>.</p>
                <ul className="landing-account-list">
                  {DEMO_ACCOUNTS.map((a) => (
                    <li key={a.email}>
                      <span className="landing-account-glyph" aria-hidden="true">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="8" r="3.5"/><path d="M4 20c2-5 5-7 8-7s6 2 8 7"/></svg>
                      </span>
                      <div>
                        <div className="landing-account-row">
                          <span className="landing-mono">{a.email}</span>
                          <span className="landing-role">{a.role}</span>
                        </div>
                        <p>{a.use}</p>
                      </div>
                    </li>
                  ))}
                </ul>
              </article>

              <article className="landing-demo-block">
                <div className="landing-demo-head">
                  <span className="landing-demo-icon" aria-hidden="true">
                    <img src="/brand/icons/heart-pulse.svg" alt="" width={28} height={28} />
                  </span>
                  <div>
                    <span className="landing-demo-step">Step 02</span>
                    <h3>Open a labelled replay</h3>
                  </div>
                </div>
                <p>From Patients, enter Live console on a DEMO patient and start or resume the stream.</p>
                <ul className="landing-patient-list">
                  {DEMO_PATIENTS.map((p, i) => (
                    <li key={p.record}>
                      <span className={cx('landing-patient-glyph', `tone-${i}`)} aria-hidden="true">
                        <svg viewBox="0 0 48 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M2 12h8l4-8 5 16 4-8h11" />
                        </svg>
                      </span>
                      <div>
                        <strong>{p.name}</strong>
                        <span className="landing-mono">{p.record}</span>
                        <p>{p.focus}</p>
                      </div>
                    </li>
                  ))}
                </ul>
              </article>

              <article className="landing-demo-block">
                <div className="landing-demo-head">
                  <span className="landing-demo-icon" aria-hidden="true">
                    <img src="/brand/icons/brain-ai.svg" alt="" width={28} height={28} />
                  </span>
                  <div>
                    <span className="landing-demo-step">Step 03</span>
                    <h3>Follow the evidence</h3>
                  </div>
                </div>
                <ol className="landing-steps">
                  {[
                    { icon: '/brand/icons/activity.svg', text: 'Watch beat markers and the ribbon update as the strip advances.' },
                    { icon: '/brand/icons/shield-check.svg', text: 'Read the risk tier and the findings that contributed points.' },
                    { icon: '/brand/icons/stethoscope.svg', text: 'Open Alerts when a rule fires — the snapshot stays attached.' },
                    { icon: '/brand/icons/brain-ai.svg', text: 'Ask Insights what changed; answers stay cited to retrieved context.' },
                  ].map((step) => (
                    <li key={step.text}>
                      <span className="landing-step-icon" aria-hidden="true">
                        <img src={step.icon} alt="" width={18} height={18} />
                      </span>
                      <span>{step.text}</span>
                    </li>
                  ))}
                </ol>
                <Link to="/login" className="btn-primary mt-6 inline-flex">
                  Start at sign in
                </Link>
              </article>
            </div>
          </div>
        </section>

        <section className="landing-cta-band" aria-labelledby="cta-heading">
          <div className="landing-section-inner landing-cta-inner landing-center">
            <h2 id="cta-heading">Ready to walk the full rhythm loop?</h2>
            <p>Open the monitoring console with your demo account — no obligations, just a clear walkthrough.</p>
            <div className="landing-actions landing-actions-center">
              <Link to="/login" className="btn-primary">
                Go to sign in
              </Link>
              <a href="#demo" className="landing-btn-ghost">
                Review demo steps
              </a>
            </div>
          </div>
        </section>
      </main>

      <footer className="landing-footer">
        <div className="landing-footer-inner landing-footer-grid">
          <div>
            <BrandLogo size="sm" withWordmark onDark />
            <p className="landing-footer-blurb">Smarter healthcare. Healthier tomorrow.</p>
            <p>{DISCLAIMER}</p>
          </div>
          <div>
            <h3>Quick links</h3>
            <ul>
              {NAV.map((item) => (
                <li key={item.href}><a href={item.href}>{item.label}</a></li>
              ))}
            </ul>
          </div>
          <div>
            <h3>Console</h3>
            <ul>
              <li><Link to="/login">Sign in</Link></li>
              <li><a href="#demo">Demo accounts</a></li>
            </ul>
          </div>
        </div>
        <div className="landing-footer-bottom">
          <p className="landing-copy">© 2026 Rhythmiq. All rights reserved.</p>
        </div>
      </footer>

      <button
        type="button"
        className={cx('landing-top', showTop && 'is-visible')}
        aria-label="Back to top"
        onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
      >
        ↑
      </button>
    </div>
  );
}
