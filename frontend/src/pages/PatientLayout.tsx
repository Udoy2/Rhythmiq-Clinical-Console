import { useEffect, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, NavLink, Outlet, useOutletContext, useParams } from 'react-router-dom';
import { api, qk } from '../api/endpoints';
import type { Patient } from '../api/types';
import { SignalStatusBadge } from '../components/SignalStatus';
import { DemoBadge, ErrorState, LoadingState, TierBadge } from '../components/ui';
import { LiveFeed } from '../live/liveFeed';
import { ageFromDob, cx } from '../lib/format';
import { canUseCopilot, useAuth } from '../state/auth';

/** Feed is owned here so History / Insights / Reports do not tear down the live socket. */
export interface PatientCtx { patient: Patient; feed: LiveFeed }
export const usePatient = () => useOutletContext<PatientCtx>();

export function PatientLayout() {
  const { id = '' } = useParams();
  const role = useAuth((s) => s.user?.role);
  const q = useQuery({ queryKey: qk.patient(id), queryFn: () => api.patient(id), refetchInterval: 10_000 });

  const feed = useMemo(() => new LiveFeed(id), [id]);
  useEffect(() => {
    feed.start();
    return () => feed.stop();
  }, [feed]);

  if (q.isPending) return <LoadingState label="Loading patient…" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} title="Could not load this patient" />;
  const p = q.data;
  const age = ageFromDob(p.dob);
  const tabs = [
    { to: 'live', label: 'Live console', show: true },
    { to: 'history', label: 'History & trends', show: true },
    { to: 'insights', label: 'AI insights', show: canUseCopilot(role) },
    { to: 'reports', label: 'Reports', show: true },
  ];

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="min-w-0">
          <nav aria-label="Breadcrumb" className="text-xs text-fg-muted">
            <Link to="/patients" className="hover:text-fg hover:underline">Patients</Link> <span aria-hidden="true">/</span>
          </nav>
          <h1 className="flex flex-wrap items-center gap-2">
            <span className="truncate">{p.name}</span>
            {p.is_demo && <DemoBadge />}
          </h1>
          <div className="text-xs text-fg-muted">
            {[p.mrn ? `MRN ${p.mrn}` : null, age !== null ? `${age} y` : null, p.sex, p.device_id ? `Device ${p.device_id}` : 'No device paired'].filter(Boolean).join(' · ')}
          </div>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-3">
          <SignalStatusBadge status={p.signal_status} />
          <TierBadge tier={p.risk_tier} score={p.risk_score} />
        </div>
      </div>
      <nav aria-label="Patient sections" className="mb-3 flex gap-1 overflow-x-auto border-b border-line">
        {tabs.filter((t) => t.show).map((t) => (
          <NavLink key={t.to} to={t.to}
            className={({ isActive }) => cx('-mb-px whitespace-nowrap border-b-2 px-3 py-1.5 text-sm', isActive ? 'border-teal font-semibold text-fg' : 'border-transparent text-fg-muted hover:text-fg')}>
            {t.label}
          </NavLink>
        ))}
      </nav>
      {/* key remounts child routes when the patient changes so Live pause / insights cannot leak */}
      <Outlet key={id} context={{ patient: p, feed } satisfies PatientCtx} />
    </div>
  );
}
