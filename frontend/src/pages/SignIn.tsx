import { useState, type FormEvent } from 'react';
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom';
import { ApiError, errorText } from '../api/client';
import { api } from '../api/endpoints';
import { BrandLogo } from '../components/BrandLogo';
import { EcgStrip } from '../components/landing/EcgStrip';
import { Banner, Button, Field } from '../components/ui';
import { Disclaimer, MockBadge } from '../layout/AppShell';
import { useAuth } from '../state/auth';
import { DEMO_ACCOUNTS, DEMO_PASSWORD, SHOW_DEMO } from '../lib/demo';
import { safeRedirect } from '../lib/safeRedirect';

const POINTS = [
  'Live ECG with beat labels and rhythm rules',
  'Risk tiers with evidence — never colour alone',
  'AI insight grounded in the patient record',
];

/** The API "asks for" an OTP by rejecting the login with a message (or flag) that mentions it. */
export function wantsOtp(e: unknown): boolean {
  if (!(e instanceof ApiError) || e.status < 400 || e.status >= 500) return false;
  const body = e.body as { mfa_required?: unknown; otp_required?: unknown } | undefined;
  if (body && (body.mfa_required === true || body.otp_required === true)) return true;
  return /\b(otp|mfa|totp|two[- ]factor|one[- ]time|verification code)\b/i.test(e.detail);
}

export function SignInPage() {
  const token = useAuth((s) => s.accessToken);
  const setSession = useAuth((s) => s.setSession);
  const nav = useNavigate();
  const loc = useLocation();
  const from = safeRedirect((loc.state as { from?: string } | null)?.from);

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [otp, setOtp] = useState('');
  const [needOtp, setNeedOtp] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (token) return <Navigate to={from} replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.login({ email: email.trim(), password, ...(needOtp && otp ? { otp: otp.trim() } : {}) });
      setSession({ accessToken: res.access_token, refreshToken: res.refresh_token, user: res.user });
      nav(from, { replace: true });
    } catch (err) {
      if (wantsOtp(err)) {
        setError(needOtp ? errorText(err) : null);
        setNeedOtp(true);
      } else {
        setError(errorText(err));
      }
    } finally {
      setBusy(false);
    }
  };

  const showDemo = SHOW_DEMO;

  return (
    <div className="flex min-h-screen flex-col">
      <main className="auth-shell flex-1">
        <section className="auth-brand hidden lg:flex">
          <div>
            <BrandLogo size="lg" withWordmark className="mb-10" />
            <p className="max-w-md text-4xl font-extrabold leading-tight tracking-tight text-ink">
              See every beat.<br />
              <span className="text-accent-text">Act on evidence.</span>
            </p>
            <p className="mt-4 max-w-sm text-base text-fg-muted">
              Stream, classify and escalate: every beat labelled, every alert tied to its evidence.
            </p>
            <ul className="mt-10 space-y-3">
              {POINTS.map((item) => (
                <li key={item} className="flex items-start gap-3 text-sm text-fg">
                  <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent-dim text-accent-text" aria-hidden="true">
                    <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                      <path d="M2.5 6.2L4.8 8.5L9.5 3.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </span>
                  {item}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <EcgStrip className="mb-6" />
            <p className="text-xs text-fg-muted">Research and demonstration system — not a medical device.</p>
          </div>
        </section>

        <section className="auth-form">
          <div className="mx-auto w-full max-w-md">
            <div className="mb-6 flex items-center gap-2 lg:hidden">
              <BrandLogo size="sm" withWordmark />
              <MockBadge />
            </div>
            <div className="mb-2 flex items-center gap-3">
              <Link to="/" className="text-xs font-semibold text-accent-text hover:underline">← Rhythmiq home</Link>
              <span className="hidden lg:inline"><MockBadge /></span>
            </div>
            <h1 className="text-2xl font-extrabold tracking-tight text-ink">Welcome back</h1>
            <p className="mt-1 text-sm text-fg-muted">Sign in to the Rhythmiq monitoring console.</p>

            <form onSubmit={submit} className="panel mt-6 space-y-3 p-5" aria-label="Sign in">
              {error && <Banner tone="critical" title="Sign-in failed.">{error}</Banner>}
              <Field label="Email">
                {(id) => <input id={id} className="input" type="email" autoComplete="username" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} />}
              </Field>
              <Field label="Password">
                {(id) => <input id={id} className="input" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />}
              </Field>
              {needOtp && (
                <Field label="One-time code" hint="Enter the 6-digit code from your authenticator app.">
                  {(id, desc) => (
                    <input id={id} aria-describedby={desc} className="input num tracking-[0.3em]" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}"
                      maxLength={6} required autoFocus value={otp} onChange={(e) => setOtp(e.target.value.replace(/\D/g, ''))} />
                  )}
                </Field>
              )}
              <Button type="submit" variant="primary" busy={busy} className="w-full">{needOtp ? 'Verify and sign in' : 'Sign in'}</Button>
            </form>

            {showDemo && (
              <section className="panel mt-4 p-3" aria-label="Demo accounts">
                <h2 className="text-xs text-fg-muted">Demo accounts — password <span className="num text-fg">{DEMO_PASSWORD}</span></h2>
                <ul className="mt-2 grid grid-cols-1 gap-1">
                  {DEMO_ACCOUNTS.map((a) => (
                    <li key={a.email}>
                      <button
                        type="button"
                        onClick={() => { setEmail(a.email); setPassword(DEMO_PASSWORD); setNeedOtp(false); setOtp(''); setError(null); }}
                        className="flex w-full items-center justify-between rounded px-2 py-1.5 text-left text-xs hover:bg-paper-sunk"
                      >
                        <span className="num text-fg">{a.email}</span>
                        <span className="text-fg-muted">{a.role}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </div>
        </section>
      </main>
      <Disclaimer className="lg:hidden" />
    </div>
  );
}
