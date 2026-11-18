import { useEffect, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { NavLink, useNavigate } from 'react-router-dom';
import { api, qk } from '../api/endpoints';
import { transport } from '../api/transport';
import { DISCLAIMER } from '../api/types';
import { Badge, Button } from '../components/ui';
import { Toaster } from '../components/ui/Toaster';
import { cx } from '../lib/format';
import { canManageDevices, canSeeAlerts, isAdmin, ROLE_LABEL, useAuth } from '../state/auth';

export function Disclaimer({ className }: { className?: string }) {
  return (
    <footer role="contentinfo" className={cx('border-t border-line bg-ink-950 px-4 py-1.5 text-center text-xs text-fg-muted', className)}>
      {DISCLAIMER}
    </footer>
  );
}

export function MockBadge() {
  if (!transport.isMock) return null;
  return <Badge tone="amber" title="VITE_MOCK=1 — REST and live data are simulated in the browser; nothing here comes from the backend or a model">MOCK DATA</Badge>;
}

function Logo() {
  return (
    <span className="flex items-center gap-2 font-semibold tracking-tight text-fg">
      <svg width="22" height="16" viewBox="0 0 22 16" aria-hidden="true">
        <path d="M0 9h5l2-3 2.5 8 3-13 2.5 10 1.5-2H22" fill="none" stroke="#00A896" strokeWidth="1.8" strokeLinejoin="round" strokeLinecap="round" />
      </svg>
      CardioSense AI
    </span>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { user, refreshToken, clear, setUser } = useAuth();
  const nav = useNavigate();
  const qc = useQueryClient();

  // Validate the stored session and keep the user object current (role changes, MFA).
  const me = useQuery({ queryKey: qk.me, queryFn: api.me, staleTime: 60_000 });
  useEffect(() => { if (me.data) setUser(me.data); }, [me.data, setUser]);

  const openAlerts = useQuery({
    queryKey: qk.alerts({ state: 'open' }),
    queryFn: () => api.alerts({ state: 'open', evidence: false }),
    enabled: canSeeAlerts(user?.role),
    refetchInterval: 15_000,
  });

  const signOut = async () => {
    try { await api.logout(refreshToken); } catch { /* sign out locally regardless */ }
    clear(true); // explicit: the next person to sign in must not land on this user's last page
    qc.clear();
    nav('/login', { replace: true, state: null });
  };

  const role = user?.role;
  const links: { to: string; label: string; show: boolean; count?: number }[] = [
    { to: '/patients', label: 'Patients', show: true },
    { to: '/alerts', label: 'Alerts', show: canSeeAlerts(role), count: openAlerts.data?.length },
    { to: '/admin', label: isAdmin(role) ? 'Admin' : 'Devices', show: isAdmin(role) || canManageDevices(role) },
    { to: '/about', label: 'About the model', show: true },
  ];

  return (
    <div className="flex min-h-screen flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-teal focus:px-3 focus:py-1.5 focus:text-ink-950">
        Skip to content
      </a>
      <header className="sticky top-0 z-40 border-b border-line bg-navy">
        <div className="mx-auto flex h-12 max-w-[1680px] items-center gap-4 px-4">
          <Logo />
          <MockBadge />
          <nav aria-label="Primary" className="flex h-full min-w-0 flex-1 items-stretch gap-1 overflow-x-auto">
            {links.filter((l) => l.show).map((l) => (
              <NavLink
                key={l.to}
                to={l.to}
                className={({ isActive }) => cx(
                  'flex items-center gap-1.5 whitespace-nowrap border-b-2 px-2.5 text-sm',
                  isActive ? 'border-teal font-semibold text-fg' : 'border-transparent text-fg-muted hover:text-fg',
                )}
              >
                {l.label}
                {typeof l.count === 'number' && l.count > 0 && (
                  <span className="num rounded-sm bg-coral-strong px-1 text-2xs font-semibold text-white" aria-label={`${l.count} open`}>{l.count}</span>
                )}
              </NavLink>
            ))}
          </nav>
          {user && (
            <div className="flex shrink-0 items-center gap-3">
              <div className="hidden text-right leading-tight sm:block">
                <div className="max-w-[14rem] truncate text-sm text-fg">{user.name || user.email}</div>
                <div className="text-2xs text-fg-muted">{ROLE_LABEL[user.role] ?? user.role}</div>
              </div>
              <Button size="sm" onClick={() => void signOut()}>Sign out</Button>
            </div>
          )}
        </div>
      </header>
      <main id="main" className="mx-auto w-full max-w-[1680px] flex-1 px-4 py-4">{children}</main>
      <Disclaimer className="sticky bottom-0 z-30" />
      <Toaster />
    </div>
  );
}
