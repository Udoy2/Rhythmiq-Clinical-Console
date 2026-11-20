import { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { errorText } from '../../api/client';
import { api } from '../../api/endpoints';
import type { Alert } from '../../api/types';
import { fmtAgo } from '../../lib/format';
import { toast } from '../../state/toasts';
import { TIER_STYLE, tierLabel, tierTitle } from '../ecg/beatStyle';
import { Button, ShapeGlyph, SideSection } from '../ui';

const UNDO_MS = 8000;

export const STATE_LABEL: Record<string, string> = { open: 'Open', acknowledged: 'Acknowledged', escalated: 'Escalated', closed: 'Closed' };

export function AlertsPanel({ alerts, canAct, onUpdated, loading, error }: {
  alerts: Alert[]; canAct: boolean; onUpdated: (a: Alert) => void; loading?: boolean; error?: unknown;
}) {
  const qc = useQueryClient();
  // `undoing`: Watch acks inside the 8 s undo window. `sending`: request in flight. Urgent/Critical skip the window.
  const [undoing, setUndoing] = useState<ReadonlySet<string>>(new Set());
  const [sending, setSending] = useState<ReadonlySet<string>>(new Set());
  const timers = useRef(new Map<string, { t: ReturnType<typeof setTimeout>; alert: Alert }>());
  const onUpdatedRef = useRef(onUpdated);
  onUpdatedRef.current = onUpdated;
  const alertsRef = useRef(alerts);
  alertsRef.current = alerts;

  const mark = (set: typeof setUndoing, id: string, on: boolean) =>
    set((w) => { const n = new Set(w); if (on) n.add(id); else n.delete(id); return n; });
  const send = useCallback((a: Alert) => {
    timers.current.delete(a.id);
    mark(setUndoing, a.id, false);
    // Someone else may have acknowledged it during the undo window.
    const now = alertsRef.current.find((x) => x.id === a.id);
    if (now && now.state !== 'open') return;
    mark(setSending, a.id, true);
    api.alertAction(a.id, 'ack', '')
      .then((upd) => {
        onUpdatedRef.current(upd);
        void qc.invalidateQueries({ queryKey: ['alerts'] });
        void qc.invalidateQueries({ queryKey: ['patients'] });
        toast.ok('Alert acknowledged', upd.title);
      })
      .catch((e) => toast.error('Could not acknowledge the alert', errorText(e)))
      .finally(() => mark(setSending, a.id, false));
  }, [qc]);

  // Leaving the page sends any acknowledgement still waiting out its undo window.
  useEffect(() => () => { timers.current.forEach(({ t, alert }) => { clearTimeout(t); send(alert); }); }, [send]);

  // A reload or tab close would drop it, so ask the browser to confirm while one is waiting.
  useEffect(() => {
    if (undoing.size === 0) return;
    const guard = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, [undoing.size]);

  const onAck = (a: Alert) => {
    const pending = timers.current.get(a.id);
    if (pending) { clearTimeout(pending.t); timers.current.delete(a.id); mark(setUndoing, a.id, false); return; }
    if (a.tier === 'red' || a.tier === 'orange') { send(a); return; }
    mark(setUndoing, a.id, true);
    timers.current.set(a.id, { t: setTimeout(() => send(a), UNDO_MS), alert: a });
  };
  const active = alerts.filter((a) => a.state !== 'closed');

  return (
    <SideSection
      title={<h2 className="text-sm font-bold tracking-tight">Alerts <span className="num ml-1 text-fg-muted">{active.length}</span></h2>}
      right={<Link to="/alerts" className="text-xs text-accent-text hover:underline">Alerts centre</Link>}
    >
      {active.length === 0 ? (
        <p className="text-sm text-fg-muted">
          {loading ? 'Loading alerts…' : error ? `Could not load earlier alerts: ${errorText(error)}` : 'No open alerts for this patient.'}
        </p>
      ) : (
        <ul className="-mx-4 max-h-72 divide-y divide-line/70 overflow-y-auto" aria-live="polite">
          {active.map((a) => {
            const st = TIER_STYLE[a.tier] ?? TIER_STYLE.amber;
            return (
              <li
                key={a.id}
                className="px-4 py-2"
                style={{ boxShadow: `inset ${a.tier === 'red' ? 8 : 3}px 0 0 ${st.color}`, background: a.tier === 'red' || a.tier === 'orange' ? st.bg : undefined }}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-x-1.5 text-2xs">
                      <span title={tierTitle(a.tier)} className="inline-flex items-center gap-1.5 whitespace-nowrap font-semibold" style={{ color: st.text }}>
                        <ShapeGlyph shape={st.shape} color={st.color} size={10} />
                        {tierLabel(a.tier)}
                      </span>
                      <span className="whitespace-nowrap text-fg-muted">{STATE_LABEL[a.state] ?? a.state} · {fmtAgo(a.created_at)}</span>
                    </div>
                    <Link to={`/alerts/${a.id}`} className="block truncate text-sm font-medium text-fg hover:underline">{a.title}</Link>
                    <p className="line-clamp-2 text-xs text-fg-muted">{a.detail}</p>
                  </div>
                  {canAct && a.state === 'open' && (
                    <Button
                      size="sm"
                      variant={undoing.has(a.id) ? 'secondary' : 'primary'}
                      busy={sending.has(a.id)}
                      onClick={() => onAck(a)}
                      title={undoing.has(a.id) ? 'Recorded in 8 s unless you undo' : undefined}
                      aria-label={`${undoing.has(a.id) ? 'Undo acknowledge' : 'Acknowledge'} alert: ${a.title}`}
                    >
                      {undoing.has(a.id) ? 'Undo' : 'Acknowledge'}
                    </Button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </SideSection>
  );
}
