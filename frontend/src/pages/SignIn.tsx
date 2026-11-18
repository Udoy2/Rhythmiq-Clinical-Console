import { useState, type FormEvent } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { ApiError, errorText } from '../api/client';
import { api } from '../api/endpoints';
import { transport } from '../api/transport';
import { Banner, Button, Field } from '../components/ui';
import { Disclaimer, MockBadge } from '../layout/AppShell';
import { useAuth } from '../state/auth';

const DEMO_PASSWORD = 'CardioSense!23';
const DEMO_ACCOUNTS = [
  { email: 'clinician@cardiosense.dev', role: 'Clinician' },
  { email: 'admin@cardiosense.dev', role: 'Org admin' },
  { email: 'tech@cardiosense.dev', role: 'Technician' },
  { email: 'patient@cardiosense.dev', role: 'Patient' },
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
  const from = (loc.state as { from?: string } | null)?.from ?? '/patients';

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

  const showDemo = import.meta.env.DEV || transport.isMock;

  return (
    <div className="flex min-h-screen flex-col">
      <main className="flex flex-1 items-center justify-center px-4 py-10">
        <div className="w-full max-w-sm">
          <div className="mb-5 flex items-center gap-2">
            <svg width="30" height="22" viewBox="0 0 22 16" aria-hidden="true">
              <path d="M0 9h5l2-3 2.5 8 3-13 2.5 10 1.5-2H22" fill="none" stroke="#00A896" strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" />
            </svg>
            <div>
              <h1 className="leading-tight">CardioSense AI</h1>
              <p className="text-xs text-fg-muted">ECG rhythm monitoring console</p>
            </div>
            <span className="ml-auto"><MockBadge /></span>
          </div>

          <form onSubmit={submit} className="panel space-y-3 p-4" aria-label="Sign in">
            <h2 className="text-base">Sign in</h2>
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
            <section className="mt-4 rounded-md border border-dashed border-line p-3" aria-label="Demo accounts">
              <h2 className="text-xs text-fg-muted">Demo accounts (development seed) — password <span className="font-mono text-fg">{DEMO_PASSWORD}</span></h2>
              <ul className="mt-2 grid grid-cols-1 gap-1">
                {DEMO_ACCOUNTS.map((a) => (
                  <li key={a.email}>
                    <button
                      type="button"
                      onClick={() => { setEmail(a.email); setPassword(DEMO_PASSWORD); setNeedOtp(false); setOtp(''); setError(null); }}
                      className="flex w-full items-center justify-between rounded px-2 py-1 text-left text-xs hover:bg-ink-700"
                    >
                      <span className="font-mono text-fg">{a.email}</span>
                      <span className="text-fg-muted">{a.role}</span>
                    </button>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-2xs text-fg-faint">Selecting an account fills the form; press Sign in to continue.</p>
            </section>
          )}
        </div>
      </main>
      <Disclaimer />
    </div>
  );
}
