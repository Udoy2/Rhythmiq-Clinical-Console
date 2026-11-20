import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { api, qk } from '../api/endpoints';
import { ALERT_STATES, TIERS, type Alert, type AlertState, type Tier } from '../api/types';
import { TIER_STYLE } from '../components/ecg/beatStyle';
import { STATE_LABEL } from '../components/live/AlertsPanel';
import { EmptyState, Mono, PageHeader, QueryState, Select, TierBadge } from '../components/ui';
import { fmtAgo, fmtDateTime } from '../lib/format';
import { sortAlerts } from '../live/liveReducer';

export default function AlertsPage() {
  const [state, setState] = useState<AlertState | ''>('open');
  const [tier, setTier] = useState<Tier | ''>('');
  const nav = useNavigate();
  const filters = { state, tier };
  const q = useQuery({ queryKey: qk.alerts(filters), queryFn: () => api.alerts({ ...filters, evidence: false }), refetchInterval: 10_000 });
  const patients = useQuery({ queryKey: qk.patients(), queryFn: () => api.patients() });
  const nameOf = (a: Alert) => a.patient_name ?? patients.data?.find((p) => p.id === a.patient_id)?.name ?? a.patient_id;

  return (
    <div>
      <PageHeader
        title="Alerts centre"
        sub="Critical first. Alerts are never closed automatically — every one needs a clinician decision."
        right={
          <>
            <div>
              <label htmlFor="f-state" className="label mr-1.5">State</label>
              <Select id="f-state" className="!w-40" value={state} onChange={(e) => setState(e.target.value as AlertState | '')}>
                <option value="">All states</option>
                {ALERT_STATES.map((s) => <option key={s} value={s}>{STATE_LABEL[s]}</option>)}
              </Select>
            </div>
            <div>
              <label htmlFor="f-tier" className="label mr-1.5">Tier</label>
              <Select id="f-tier" className="!w-44" value={tier} onChange={(e) => setTier(e.target.value as Tier | '')}>
                <option value="">All tiers</option>
                {[...TIERS].reverse().map((t) => <option key={t} value={t}>{TIER_STYLE[t].meaning}</option>)}
              </Select>
            </div>
          </>
        }
      />
      <div className="panel overflow-x-auto">
        <QueryState query={q} loadingLabel="Loading alerts…"
          empty={<EmptyState title="No alerts match these filters">{state === 'open' ? 'There is nothing waiting for review.' : 'Try a different state or tier.'}</EmptyState>}>
          {(list) => (
            <table>
              <caption className="sr-only">Alert queue, critical first</caption>
              <thead>
                <tr><th scope="col">Tier</th><th scope="col">Alert</th><th scope="col">Patient</th><th scope="col">Raised</th><th scope="col">State</th><th scope="col">Rule</th><th scope="col"><span className="sr-only">Open</span></th></tr>
              </thead>
              <tbody>
                {sortAlerts(list).map((a) => (
                  <tr key={a.id} className="cursor-pointer hover:bg-paper-sunk" onClick={() => nav(`/alerts/${a.id}`)}
                    style={{ boxShadow: `inset 3px 0 0 ${(TIER_STYLE[a.tier] ?? TIER_STYLE.amber).color}` }}>
                    <td><TierBadge tier={a.tier} /></td>
                    <td className="max-w-[26rem]">
                      <Link to={`/alerts/${a.id}`} className="font-medium text-fg hover:underline" onClick={(e) => e.stopPropagation()}>{a.title}</Link>
                      <div className="truncate text-xs text-fg-muted">{a.detail}</div>
                    </td>
                    <td><Link to={`/patients/${a.patient_id}/live`} className="hover:underline" onClick={(e) => e.stopPropagation()}>{nameOf(a)}</Link></td>
                    <td className="whitespace-nowrap"><div className="num">{fmtDateTime(a.created_at)}</div><div className="text-xs text-fg-muted">{fmtAgo(a.created_at)}</div></td>
                    <td className="whitespace-nowrap">
                      {STATE_LABEL[a.state] ?? a.state}
                      {a.acknowledged_by && <div className="text-xs text-fg-muted">by {a.acknowledged_by}</div>}
                    </td>
                    <td><Mono>{a.rule_id}</Mono></td>
                    <td className="text-right"><Link to={`/alerts/${a.id}`} className="text-xs font-medium text-accent-text hover:underline" onClick={(e) => e.stopPropagation()}>Evidence<span className="sr-only"> for {a.title}</span></Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </QueryState>
      </div>
    </div>
  );
}
