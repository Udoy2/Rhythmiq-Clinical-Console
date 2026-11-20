import { useEffect, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { NavLink, useNavigate } from 'react-router-dom';
import { api, qk } from '../api/endpoints';
import { transport } from '../api/transport';
import { DISCLAIMER } from '../api/types';
import { BrandLogo } from '../components/BrandLogo';
import { DrRhidoyChathead } from '../components/DrRhidoyChathead';
import { Badge, Button } from '../components/ui';
import { Toaster } from '../components/ui/Toaster';
import { cx } from '../lib/format';
import { canManageDevices, canSeeAlerts, isAdmin, ROLE_LABEL, useAuth } from '../state/auth';

export function Disclaimer({ className }: { className?: string }) {
  return (
    <footer role="contentinfo" className={cx('border-t border-line bg-paper-sunk px-4 py-2 text-center text-xs font-medium text-fg-muted', className)}>
      {DISCLAIMER}
    </footer>
  );
}

export function MockBadge() {
  if (!transport.isMock) return null;
  return <Badge tone="amber" title="VITE_MOCK=1 — REST and live data are simulated in the browser; nothing here comes from the backend or a model">MOCK DATA</Badge>;
}

export function AppShell({ children }: { children: ReactNode }) {
  const { user, refreshToken, clear, setUser } = useAuth();
  const nav = useNavigate();
  const qc = useQueryClient();

  const me = useQuery({ queryKey: qk.me, queryFn: api.me, staleTime: 60_000 });
  useEffect(() => { if (me.data) setUser(me.data); }, [me.data, setUser]);

  const openAlerts = useQuery({
    queryKey: qk.alerts({ state: 'open' }),
    queryFn: () => api.alerts({ state: 'open', evidence: false }),
    enabled: canSeeAlerts(user?.role),
    refetchInterval: 15_000,
  });

  const signOut = async () => {
    try { await api.logout(refreshToken); } catch { /* local sign-out anyway */ }
    clear(true);
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

  const navClass = ({ isActive }: { isActive: boolean }) => cx('shell-link', isActive && 'is-active');

  return (
    <div className="flex min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-full focus:bg-ink focus:px-3 focus:py-1.5 focus:text-paper">
        Skip to content
      </a>

      <aside className="shell-rail sticky top-0 hidden h-screen w-60 shrink-0 flex-col lg:flex" aria-label="Primary">
        <div className="flex flex-col items-start gap-2 px-4 py-5">
          <BrandLogo size="sm" withWordmark />
          <MockBadge />
        </div>
        <nav className="flex flex-1 flex-col gap-1 px-2.5">
          {links.filter((l) => l.show).map((l) => (
            <NavLink key={l.to} to={l.to} end={l.to === '/patients'} className={navClass}>
              <span>{l.label}</span>
              {typeof l.count === 'number' && l.count > 0 && (
                <span className="num rounded-full bg-alert-strong px-1.5 text-xs font-semibold text-white" aria-label={`${l.count} open`}>{l.count}</span>
              )}
            </NavLink>
          ))}
        </nav>
        {user && (
          <div className="m-3 rounded-md border border-line bg-paper-raised p-3">
            <div className="truncate text-sm text-fg">{user.name || user.email}</div>
            <div className="text-xs text-fg-muted">{ROLE_LABEL[user.role] ?? user.role}</div>
            <Button size="sm" className="mt-3 w-full" onClick={() => void signOut()}>Sign out</Button>
          </div>
        )}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-40 border-b border-line bg-paper-sunk lg:hidden">
          <div className="flex h-14 items-center gap-3 px-3">
            <BrandLogo size="sm" withWordmark />
            <MockBadge />
            <div className="ml-auto">
              {user && <Button size="sm" onClick={() => void signOut()}>Sign out</Button>}
            </div>
          </div>
          <nav aria-label="Primary" className="flex gap-0.5 overflow-x-auto px-2 pb-2">
            {links.filter((l) => l.show).map((l) => (
              <NavLink
                key={l.to}
                to={l.to}
                end={l.to === '/patients'}
                className={({ isActive }) => cx(
                  'whitespace-nowrap rounded px-2 py-1.5 text-sm font-semibold',
                  isActive ? 'bg-ink text-paper' : 'text-fg-muted',
                )}
              >
                {l.label}
                {typeof l.count === 'number' && l.count > 0 && (
                  <span className="ml-1.5 num rounded-full bg-alert-strong px-1.5 text-2xs text-white">{l.count}</span>
                )}
              </NavLink>
            ))}
          </nav>
        </header>

        <main id="main" className="mx-auto w-full max-w-[1680px] flex-1 px-4 pb-28 pt-5 sm:px-6">{children}</main>
        <Disclaimer className="sticky bottom-0 z-30" />
        <Toaster />
        <DrRhidoyChathead />
      </div>
    </div>
  );
}
