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
    <Card title="Demo replay" right={<span className="text-2xs text-fg-faint">Public labelled recordings — never mixed with patient records</span>}>
      {!isDemoPatient && (
        <p className="mb-2 text-xs text-amber-text">This is not a demo patient. A replay is always streamed to a demo patient, not to this record.</p>
      )}
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-[14rem] flex-1">
          <label htmlFor="demo-record" className="label">Recording</label>
          <Select id="demo-record" value={record} onChange={(e) => setRecord(e.target.value)} disabled={records.isPending || list.length === 0}>
            {records.isPending && <option value="">Loading recordings…</option>}
            {!records.isPending && list.length === 0 && <option value="">No recordings available</option>}
            {list.map((r) => (
              <option key={`${r.dataset}-${r.record}`} value={r.record}>
                {r.record} ({r.dataset}){r.description ? ` — ${r.description}` : ''}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <label htmlFor="demo-speed" className="label">Replay speed</label>
          <Select id="demo-speed" className="w-24" value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
            {SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}
          </Select>
        </div>
        <Button variant="primary" onClick={() => start.mutate()} busy={start.isPending} disabled={!record}>
          {running ? 'Restart replay' : 'Start replay'}
        </Button>
        <Button onClick={() => stop.mutate()} busy={stop.isPending} disabled={!running}>Stop</Button>
      </div>
      {records.isError && <p role="alert" className="mt-2 text-xs text-coral-text">Could not load the recordings: {errorText(records.error)}</p>}
      {mock && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-dashed border-line pt-3">
          <span className="text-2xs font-semibold text-amber-text">MOCK FAULTS</span>
          <Button size="sm" onClick={() => mock.leadOff(patientId, 6)} disabled={!running}>Lead-off 6 s</Button>
          <Button size="sm" onClick={() => mock.lowSqi(patientId, 6)} disabled={!running}>Low SQI 6 s</Button>
          <Button size="sm" onClick={() => mock.gap(patientId, 1.5)} disabled={!running}>Gap 1.5 s</Button>
          <Button size="sm" onClick={() => mock.dropConnection(6)}>Drop connection 6 s</Button>
        </div>
      )}
    </Card>
  );
}
