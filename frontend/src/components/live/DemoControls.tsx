import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { errorText } from '../../api/client';
import { api, qk } from '../../api/endpoints';
import type { SessionMsg } from '../../api/types';
import { toast } from '../../state/toasts';
import { Button, Card, Select } from '../ui';

const SPEEDS = [0.5, 1, 2, 4];

export function DemoControls({
  patientId, isDemoPatient, session, stream = 'unknown',
}: {
  patientId: string;
  isDemoPatient: boolean;
  session: SessionMsg | null;
  /** From live `status` — avoids Stop/Restart looking "running" when the socket reports disconnected. */
  stream?: 'connected' | 'disconnected' | 'unknown';
}) {
  const qc = useQueryClient();
  const records = useQuery({ queryKey: qk.demoRecords, queryFn: api.demoRecords, staleTime: 300_000 });
  const [record, setRecord] = useState('');
  const [speed, setSpeed] = useState(1);
  const list = records.data ?? [];
  useEffect(() => {
    if (!record && list.length) setRecord((list.find((r) => r.record === '208') ?? list[0]).record);
  }, [list, record]);

  const refresh = () => { void qc.invalidateQueries({ queryKey: ['patients'] }); void qc.invalidateQueries({ queryKey: qk.patient(patientId) }); };
  const start = useMutation({
    mutationFn: () => {
      const r = list.find((x) => x.record === record);
      return api.demoReplay({ record, dataset: r?.dataset ?? 'MIT-BIH', speed, ...(isDemoPatient ? { patient_id: patientId } : {}) });
    },
    onSuccess: (res) => {
      refresh();
      if (res.patient_id && res.patient_id !== patientId) toast.info('Replay started for another demo patient', 'Open that patient from the patient list to watch it.');
      else toast.ok(`Replay of record ${record} started`);
    },
    onError: (e) => toast.error('Could not start the replay', errorText(e)),
  });
  const stop = useMutation({
    mutationFn: () => api.demoStop(patientId),
    onSuccess: () => { refresh(); toast.info('Replay stopped'); },
    onError: (e) => toast.error('Could not stop the replay', errorText(e)),
  });
  const running = !!session?.active && session.source === 'replay' && stream !== 'disconnected';
  const mock = typeof window !== 'undefined' ? window.__cardiosenseMock : undefined;

  return (
    <Card title="Demo replay" bodyClass="space-y-2 p-2.5" right={<span className="text-2xs text-fg-faint">Public recordings</span>}>
      {!isDemoPatient && (
        <p className="text-2xs text-amber-text">Replay streams to a demo patient, not this record.</p>
      )}
      <div className="grid grid-cols-[1fr_4.5rem] gap-2">
        <div className="min-w-0">
          <label htmlFor="demo-record" className="label">Recording</label>
          <Select id="demo-record" className="text-xs" value={record} onChange={(e) => setRecord(e.target.value)} disabled={records.isPending || list.length === 0}>
            {records.isPending && <option value="">Loading…</option>}
            {!records.isPending && list.length === 0 && <option value="">None available</option>}
            {list.map((r) => (
              <option key={`${r.dataset}-${r.record}`} value={r.record}>
                {r.record} ({r.dataset}){r.description ? ` — ${r.description}` : ''}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <label htmlFor="demo-speed" className="label">Speed</label>
          <Select id="demo-speed" className="w-full text-xs" value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
            {SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}
          </Select>
        </div>
      </div>
      <div className="flex gap-1.5">
        <Button size="sm" variant="primary" className="flex-1" onClick={() => start.mutate()} busy={start.isPending} disabled={!record}>
          {running ? 'Restart' : 'Start'}
        </Button>
        <Button size="sm" className="flex-1" onClick={() => stop.mutate()} busy={stop.isPending} disabled={!running}>Stop</Button>
      </div>
      {records.isError && <p role="alert" className="text-2xs text-coral-text">Could not load recordings: {errorText(records.error)}</p>}
      {mock && (
        <div className="flex flex-wrap gap-1 border-t border-dashed border-line pt-2">
          <span className="w-full text-2xs font-semibold text-amber-text">Mock faults</span>
          <Button size="sm" onClick={() => mock.leadOff(patientId, 6)} disabled={!running}>Lead-off</Button>
          <Button size="sm" onClick={() => mock.lowSqi(patientId, 6)} disabled={!running}>Low SQI</Button>
          <Button size="sm" onClick={() => mock.gap(patientId, 1.5)} disabled={!running}>Gap</Button>
          <Button size="sm" onClick={() => mock.dropConnection(6)}>Drop</Button>
        </div>
      )}
    </Card>
  );
}
