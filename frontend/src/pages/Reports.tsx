import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { errorText, saveBlob } from '../api/client';
import { api, qk } from '../api/endpoints';
import { DISCLAIMER, TREND_RANGES, type TrendRange } from '../api/types';
import { Banner, Button, Card, EmptyState, Mono, QueryState, Segmented } from '../components/ui';
import { fmtDateTime, fmtDuration } from '../lib/format';
import { toast } from '../state/toasts';
import { sessionDuration } from './History';
import { usePatient } from './PatientLayout';

const LABEL: Record<TrendRange, string> = { '24h': 'Last 24 hours', '7d': 'Last 7 days', '30d': 'Last 30 days', '90d': 'Last 90 days' };

export default function ReportsPage() {
  const { patient } = usePatient();
  const [range, setRange] = useState<TrendRange>('7d');
  const [preview, setPreview] = useState<string | null>(null);
  const meta = useQuery({ queryKey: qk.meta, queryFn: api.meta, staleTime: 300_000 });
  const sessions = useQuery({ queryKey: qk.sessions(patient.id), queryFn: () => api.sessions(patient.id) });

  const pdf = useMutation({
    mutationFn: async (mode: 'download' | 'preview') => ({ mode, ...(await api.reportPdf(patient.id, range)) }),
    onSuccess: ({ mode, blob, filename }) => {
      if (mode === 'download') {
        saveBlob(blob, filename ?? `cardiosense-report-${patient.id}-${range}.pdf`);
        toast.ok('Report downloaded');
      } else {
        setPreview((old) => { if (old) URL.revokeObjectURL(old); return URL.createObjectURL(new Blob([blob], { type: 'application/pdf' })); });
      }
    },
  });
  const csv = useMutation({
    mutationFn: (sid: string) => api.sessionCsv(sid),
    onSuccess: ({ blob, filename }, sid) => saveBlob(blob, filename ?? `session-${sid}.csv`),
    onError: (e) => toast.error('CSV export failed', errorText(e)),
  });

  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-[24rem_minmax(0,1fr)]">
      <div className="min-w-0 space-y-3">
        <Card title="PDF report">
          <div className="label mb-1">Date range</div>
          <Segmented label="Report date range" value={range} options={TREND_RANGES} onChange={(r) => { setRange(r); setPreview(null); }} size="md"
            format={(r) => ({ '24h': '24 h', '7d': '7 d', '30d': '30 d', '90d': '90 d' })[r]} />
          <p className="mt-2 text-sm text-fg-muted">{LABEL[range]} for <span className="text-fg">{patient.name}</span>: trends, alerts with their evidence, and the versions used.</p>
          <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            <dt className="text-fg-muted">Model version</dt><dd className="text-right"><Mono>{meta.data ? (meta.data.model_loaded ? meta.data.model_version ?? '—' : 'not loaded (rules only)') : '…'}</Mono></dd>
            <dt className="text-fg-muted">Rule version</dt><dd className="text-right"><Mono>{meta.data?.rule_version ?? '…'}</Mono></dd>
          </dl>
          <p className="mt-3 rounded border border-line bg-paper-sunk px-2 py-1.5 text-xs text-fg-muted">Printed on every report: {meta.data?.disclaimer ?? DISCLAIMER}</p>
          {pdf.isError && <div className="mt-2"><Banner tone="critical" title="The report could not be generated.">{errorText(pdf.error)}</Banner></div>}
          <div className="mt-3 flex gap-2">
            <Button variant="primary" busy={pdf.isPending && pdf.variables === 'download'} onClick={() => pdf.mutate('download')}>Download PDF</Button>
            <Button busy={pdf.isPending && pdf.variables === 'preview'} onClick={() => pdf.mutate('preview')}>Preview</Button>
          </div>
        </Card>

        <Card title="CSV export" bodyClass="p-0">
          <QueryState query={sessions} empty={<EmptyState title="No sessions to export" />}>
            {(list) => (
              <ul className="max-h-80 divide-y divide-line/60 overflow-y-auto">
                {list.map((s) => (
                  <li key={s.id} className="flex items-center justify-between gap-2 px-3 py-2 text-sm">
                    <div className="min-w-0">
                      <div className="num text-fg">{fmtDateTime(s.started_at)}</div>
                      <div className="text-xs text-fg-muted">{s.source === 'replay' ? `Replay${s.record ? ` · record ${s.record}` : ''}` : 'Device'} · {fmtDuration(sessionDuration(s))}</div>
                    </div>
                    <Button size="sm" busy={csv.isPending && csv.variables === s.id} onClick={() => csv.mutate(s.id)} aria-label={`Export session started ${fmtDateTime(s.started_at)} as CSV`}>CSV</Button>
                  </li>
                ))}
              </ul>
            )}
          </QueryState>
        </Card>
      </div>

      <Card title="Preview" bodyClass="p-0" className="min-h-[28rem]">
        {preview ? (
          <iframe title="Report preview" src={preview} className="h-[70vh] w-full rounded-b-md bg-white" />
        ) : (
          <EmptyState title="No preview yet">Choose a range and press Preview to render the PDF here, or download it directly.</EmptyState>
        )}
      </Card>
    </div>
  );
}
