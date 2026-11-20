import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import { errorText } from '../api/client';
import { api, qk } from '../api/endpoints';
import type { Alert, AlertTransition } from '../api/types';
import { EvidenceStrip } from '../components/ecg/EvidenceStrip';
import { TIER_STYLE } from '../components/ecg/beatStyle';
import { STATE_LABEL } from '../components/live/AlertsPanel';
import { FindingChip } from '../components/live/RhythmCard';
import { Banner, Button, Card, DemoBadge, EmptyState, ErrorState, LoadingState, Mono, TierBadge } from '../components/ui';
import { fmtDateTimeS, fmtNum, fmtPct } from '../lib/format';
import { canActOnAlerts, useAuth } from '../state/auth';
import { toast, useToasts } from '../state/toasts';

type Action = 'ack' | 'escalate' | 'close';
const ACTION_LABEL: Record<Action, string> = { ack: 'Acknowledge', escalate: 'Escalate', close: 'Close' };
const DONE: Record<Action, string> = { ack: 'Alert acknowledged', escalate: 'Alert escalated', close: 'Alert closed' };

/** Transitions embedded by the API when available; otherwise what the alert itself records. */
function transitionsOf(a: Alert): { at: string | null; what: string; who: string | null; note: string | null }[] {
  const raw: AlertTransition[] | undefined = a.history ?? a.transitions;
  if (raw && raw.length) {
    return raw.map((t) => ({
      at: t.at ?? t.created_at ?? null,
      what: t.action ?? (t.to_state ? `${t.from_state ?? ''} → ${t.to_state}` : t.state ?? 'updated'),
      who: t.by ?? t.actor_email ?? t.actor ?? null,
      note: t.note ?? null,
    }));
  }
  const out: { at: string | null; what: string; who: string | null; note: string | null }[] = [{ at: a.created_at, what: 'created', who: 'rules engine', note: null }];
  if (a.state !== 'open') out.push({ at: a.updated_at, what: a.state, who: a.acknowledged_by, note: a.note });
  return out;
}

export default function AlertDetailPage() {
  const { alertId = '' } = useParams();
  const role = useAuth((s) => s.user?.role);
  const qc = useQueryClient();
  const q = useQuery({ queryKey: qk.alert(alertId), queryFn: () => api.alert(alertId) });
  const patient = useQuery({ queryKey: qk.patient(q.data?.patient_id ?? ''), queryFn: () => api.patient(q.data!.patient_id), enabled: !!q.data?.patient_id });
  const [note, setNote] = useState('');
  // Opening the evidence answers the toast for this alert.
  useEffect(() => { useToasts.getState().dismiss(`alert-${alertId}`); }, [alertId]);
  const act = useMutation({
    mutationFn: (action: Action) => api.alertAction(alertId, action, note.trim()),
    onSuccess: (a, action) => {
      qc.setQueryData(qk.alert(alertId), (old: Alert | undefined) => ({ ...old, ...a }));
      void qc.invalidateQueries({ queryKey: ['alerts'] });
      void qc.invalidateQueries({ queryKey: ['patients'] });
      void qc.invalidateQueries({ queryKey: qk.alert(alertId) });
      setNote('');
      toast.ok(DONE[action]);
    },
  });

  if (q.isPending) return <LoadingState label="Loading alert…" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} title="Could not load this alert" />;
  const a = q.data;
  const ev = a.evidence;
  const st = TIER_STYLE[a.tier] ?? TIER_STYLE.amber;
  const w = ev?.window ?? null;
  const closed = a.state === 'closed';
  const canAct = canActOnAlerts(role);

  return (
    <div className="space-y-3">
      <div>
        <nav aria-label="Breadcrumb" className="text-xs text-fg-muted"><Link to="/alerts" className="hover:text-fg hover:underline">Alerts centre</Link> <span aria-hidden="true">/</span></nav>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h1>{a.title}</h1>
          <TierBadge tier={a.tier} />
          <span className="rounded border border-line px-1.5 py-0.5 text-xs font-medium text-fg">{STATE_LABEL[a.state] ?? a.state}</span>
        </div>
        <p className="mt-1 text-sm text-fg-muted">
          <Link to={`/patients/${a.patient_id}/live`} className="font-medium text-fg hover:underline">{a.patient_name ?? patient.data?.name ?? 'Patient'}</Link>
          {patient.data?.is_demo && <> <DemoBadge /></>} · raised <span className="num">{fmtDateTimeS(a.created_at)}</span> · {st.action}
        </p>
      </div>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="min-w-0 space-y-3">
          <Card title="Evidence snapshot" right={<span className="text-2xs text-fg-faint">stored when the alert fired</span>}>
            {ev && Array.isArray(ev.strip_mv) && ev.strip_mv.length > 0
              ? <EvidenceStrip evidence={ev} />
              : <EmptyState title="No ECG strip was stored with this alert" />}
          </Card>
        </div>

        <div className="min-w-0 space-y-3">
          <Card title="Why this fired">
            <p className="text-sm text-fg">{a.detail || 'No description was stored.'}</p>
            {typeof ev?.reason === 'string' && /\s/.test(ev.reason.trim()) && <p className="mt-1 text-sm text-fg-muted">{ev.reason}</p>}
            <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
              <dt className="text-fg-muted">Rule</dt><dd className="text-right"><Mono className="text-fg">{a.rule_id}</Mono></dd>
              <dt className="text-fg-muted">Rule version</dt><dd className="text-right"><Mono>{ev?.rule_version ?? '—'}</Mono></dd>
              <dt className="text-fg-muted">Model version</dt><dd className="text-right"><Mono>{ev?.model_version ?? '—'}</Mono></dd>
              <dt className="text-fg-muted">Alert ID</dt><dd className="text-right"><Mono>{a.id}</Mono></dd>
            </dl>
            {w && (
              <div className="mt-3 border-t border-line pt-3">
                <div className="label mb-1.5">Rhythm window at the time</div>
                <dl className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-3">
                  <div><dt className="label">HR</dt><dd className="num">{fmtNum(w.hr_bpm)} bpm</dd></div>
                  <div><dt className="label">V burden</dt><dd className="num">{fmtPct(w.v_burden_pct)}</dd></div>
                  <div><dt className="label">S burden</dt><dd className="num">{fmtPct(w.s_burden_pct)}</dd></div>
                  <div><dt className="label">SQI</dt><dd className="num">{fmtNum(w.sqi, 2)}</dd></div>
                  <div><dt className="label">Risk</dt><dd className="num">{w.risk ? `${Math.round(w.risk.score)} / 100` : '—'}</dd></div>
                </dl>
                {w.findings && w.findings.length > 0 && (
                  <ul className="mt-2 flex flex-wrap gap-1.5">{w.findings.map((f, i) => <li key={i}><FindingChip f={f} /></li>)}</ul>
                )}
              </div>
            )}
            <Link to={`/patients/${a.patient_id}/sessions/${a.session_id}?t=${Math.max(0, ev?.start_s ?? 0).toFixed(1)}`} className="mt-3 inline-block text-sm font-medium text-teal-text hover:underline">
              Replay the session at this moment
            </Link>
          </Card>

          <Card title="Review">
            {closed ? (
              <p className="text-sm text-fg-muted">This alert is closed. Closed alerts cannot be changed.</p>
            ) : !canAct ? (
              <p className="text-sm text-fg-muted">Only clinicians and org admins can acknowledge, escalate or close alerts.</p>
            ) : (
              <form onSubmit={(e) => e.preventDefault()} className="space-y-2">
                {act.isError && <Banner tone="critical" title="The action failed.">{errorText(act.error)}</Banner>}
                <label htmlFor="alert-note" className="label">Note (stored with the decision)</label>
                <textarea id="alert-note" className="input min-h-[4.5rem]" value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} placeholder="What you saw and what you did" />
                <div className="flex flex-wrap gap-2">
                  <Button variant="primary" disabled={a.state !== 'open'} busy={act.isPending && act.variables === 'ack'} onClick={() => act.mutate('ack')}>{ACTION_LABEL.ack}</Button>
                  <Button disabled={a.state === 'escalated'} busy={act.isPending && act.variables === 'escalate'} onClick={() => act.mutate('escalate')}>{ACTION_LABEL.escalate}</Button>
                  <Button busy={act.isPending && act.variables === 'close'} onClick={() => act.mutate('close')}>{ACTION_LABEL.close}</Button>
                </div>
                <p className="text-2xs text-fg-faint">Every transition is recorded in the audit log with your account and the time.</p>
              </form>
            )}
            {a.note && <p className="mt-3 border-t border-line pt-2 text-sm"><span className="label">Latest note</span><br />{a.note}</p>}
          </Card>

          <Card title="Transitions" bodyClass="p-0">
            <ol className="divide-y divide-line/60">
              {transitionsOf(a).map((t, i) => (
                <li key={i} className="px-3 py-2 text-sm">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="font-medium capitalize text-fg">{t.what}</span>
                    <span className="num text-xs text-fg-muted">{fmtDateTimeS(t.at)}</span>
                  </div>
                  <div className="text-xs text-fg-muted">{t.who ?? '—'}{t.note ? <> — <span className="text-fg">{t.note}</span></> : null}</div>
                </li>
              ))}
            </ol>
          </Card>
        </div>
      </div>
    </div>
  );
}
