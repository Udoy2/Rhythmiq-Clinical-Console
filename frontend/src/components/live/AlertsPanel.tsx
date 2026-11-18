import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { errorText } from '../../api/client';
import { api } from '../../api/endpoints';
import type { Alert } from '../../api/types';
import { fmtAgo } from '../../lib/format';
import { toast } from '../../state/toasts';
import { TIER_STYLE, tierLabel } from '../ecg/beatStyle';
import { Button, Card, ShapeGlyph } from '../ui';

export const STATE_LABEL: Record<string, string> = { open: 'Open', acknowledged: 'Acknowledged', escalated: 'Escalated', closed: 'Closed' };

export function AlertsPanel({ alerts, canAct, onUpdated, loading, error }: {
  alerts: Alert[]; canAct: boolean; onUpdated: (a: Alert) => void; loading?: boolean; error?: unknown;
}) {
  const qc = useQueryClient();
  const ack = useMutation({
    mutationFn: (id: string) => api.alertAction(id, 'ack', ''),
    onSuccess: (a) => {
      onUpdated(a);
      void qc.invalidateQueries({ queryKey: ['alerts'] });
      void qc.invalidateQueries({ queryKey: ['patients'] });
      toast.ok('Alert acknowledged', a.title);
    },
    onError: (e) => toast.error('Could not acknowledge the alert', errorText(e)),
  });
  const active = alerts.filter((a) => a.state !== 'closed');

  return (
    <Card
      title={<h2>Alerts <span className="num ml-1 text-fg-muted">{active.length}</span></h2>}
      right={<Link to="/alerts" className="text-xs text-teal-text hover:underline">Alerts centre</Link>}
      bodyClass="p-0"
    >
      {active.length === 0 ? (
        <p className="px-3 py-4 text-sm text-fg-muted">
          {loading ? 'Loading alerts…' : error ? `Could not load earlier alerts: ${errorText(error)}` : 'No open alerts for this patient.'}
        </p>
      ) : (
        <ul className="max-h-72 divide-y divide-line/70 overflow-y-auto" aria-live="polite">
          {active.map((a) => {
            const st = TIER_STYLE[a.tier] ?? TIER_STYLE.amber;
            return (
              <li key={a.id} className="px-3 py-2" style={{ boxShadow: `inset 3px 0 0 ${st.color}` }}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-x-1.5 text-2xs">
                      <span className="inline-flex items-center gap-1.5 whitespace-nowrap font-semibold" style={{ color: st.text }}>
                        <ShapeGlyph shape={st.shape} color={st.color} size={10} />
                        {tierLabel(a.tier)}
                      </span>
                      <span className="whitespace-nowrap text-fg-muted">{STATE_LABEL[a.state] ?? a.state} · {fmtAgo(a.created_at)}</span>
                    </div>
                    <Link to={`/alerts/${a.id}`} className="block truncate text-sm font-medium text-fg hover:underline">{a.title}</Link>
                    <p className="line-clamp-2 text-xs text-fg-muted">{a.detail}</p>
                  </div>
                  {canAct && a.state === 'open' && (
                    <Button size="sm" busy={ack.isPending && ack.variables === a.id} onClick={() => ack.mutate(a.id)} aria-label={`Acknowledge alert: ${a.title}`}>
                      Acknowledge
                    </Button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
