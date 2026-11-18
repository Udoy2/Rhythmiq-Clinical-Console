import { useDeferredValue, useMemo, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { errorText } from '../api/client';
import { api, qk } from '../api/endpoints';
import { lastEventText } from '../api/normalize';
import type { Patient, PatientCreate, Tier } from '../api/types';
import { SignalStatusBadge } from '../components/SignalStatus';
import { Banner, Button, DemoBadge, Dialog, EmptyState, ErrorState, Field, PageHeader, Select, Skeleton, TierBadge } from '../components/ui';
import { ageFromDob, fmtAgo } from '../lib/format';
import { canManagePatients, useAuth } from '../state/auth';
import { toast } from '../state/toasts';

const RANK: Record<Tier, number> = { red: 3, orange: 2, amber: 1, green: 0 };
/** The API already sorts by risk; this keeps the order stable if it does not. */
export function sortByRisk(list: Patient[]): Patient[] {
  return [...list].sort((a, b) =>
    (RANK[b.risk_tier ?? 'green'] ?? 0) - (RANK[a.risk_tier ?? 'green'] ?? 0)
    || (b.risk_score ?? -1) - (a.risk_score ?? -1)
    || (b.open_alerts ?? 0) - (a.open_alerts ?? 0)
    || a.name.localeCompare(b.name));
}

export function PatientsPage() {
  const role = useAuth((s) => s.user?.role);
  const [search, setSearch] = useState('');
  const term = useDeferredValue(search.trim().toLowerCase());
  const [creating, setCreating] = useState(false);
  const nav = useNavigate();

  const q = useQuery({ queryKey: qk.patients(), queryFn: () => api.patients(), refetchInterval: 10_000 });
  const rows = useMemo(() => {
    const all = sortByRisk(q.data ?? []);
    return term ? all.filter((p) => p.name.toLowerCase().includes(term) || (p.mrn ?? '').toLowerCase().includes(term)) : all;
  }, [q.data, term]);

  return (
    <div>
      <PageHeader
        title="Patients"
        sub="Sorted by current risk — highest first."
        right={
          <>
            <label className="sr-only" htmlFor="patient-search">Search patients by name or MRN</label>
            <input id="patient-search" type="search" className="input w-64" placeholder="Search name or MRN" value={search} onChange={(e) => setSearch(e.target.value)} />
            {canManagePatients(role) && <Button variant="primary" onClick={() => setCreating(true)}>New patient</Button>}
          </>
        }
      />

      <div className="panel overflow-x-auto">
        {q.isPending ? (
          <div className="space-y-2 p-3" aria-busy="true" aria-label="Loading patients">
            {Array.from({ length: 5 }, (_, i) => <Skeleton key={i} className="h-9 w-full" />)}
          </div>
        ) : q.isError ? (
          <ErrorState error={q.error} onRetry={() => void q.refetch()} title="Could not load patients" />
        ) : rows.length === 0 ? (
          <EmptyState title={term ? 'No patients match this search' : 'No patients yet'}>
            {term ? 'Check the spelling or clear the search.' : canManagePatients(role) ? 'Create a patient to start monitoring.' : 'No patients are assigned to your account.'}
          </EmptyState>
        ) : (
          <table>
            <caption className="sr-only">Patients sorted by risk</caption>
            <thead>
              <tr>
                <th scope="col">Patient</th>
                <th scope="col">Risk</th>
                <th scope="col">Last event</th>
                <th scope="col">Signal</th>
                <th scope="col" className="text-right">Open alerts</th>
                <th scope="col"><span className="sr-only">Open</span></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => {
                const ev = lastEventText(p.last_event);
                const age = ageFromDob(p.dob);
                return (
                  <tr key={p.id} className="cursor-pointer hover:bg-ink-800" onClick={() => nav(`/patients/${p.id}/live`)}>
                    <td>
                      <div className="flex items-center gap-2">
                        <Link to={`/patients/${p.id}/live`} className="font-medium text-fg hover:underline" onClick={(e) => e.stopPropagation()}>{p.name}</Link>
                        {p.is_demo && <DemoBadge />}
                        {!p.consent && <span className="rounded border border-amber/60 px-1 text-2xs font-semibold text-amber-text" title="Consent is required before streaming">NO CONSENT</span>}
                      </div>
                      <div className="text-xs text-fg-muted">
                        {[p.mrn ? `MRN ${p.mrn}` : null, age !== null ? `${age} y` : null, p.sex].filter(Boolean).join(' · ') || '—'}
                      </div>
                    </td>
                    <td><TierBadge tier={p.risk_tier} score={p.risk_score} /></td>
                    <td>
                      {ev ? (
                        <div>
                          <div className="text-fg">{ev.text}</div>
                          {ev.time && <div className="text-xs text-fg-muted">{fmtAgo(ev.time)}</div>}
                        </div>
                      ) : <span className="text-fg-faint">None recorded</span>}
                    </td>
                    <td><SignalStatusBadge status={p.signal_status} /></td>
                    <td className="text-right">
                      {p.open_alerts > 0
                        ? <span className="num rounded-sm bg-coral-strong px-1.5 py-0.5 text-xs font-semibold text-white">{p.open_alerts}<span className="sr-only"> open alerts</span></span>
                        : <span className="num text-fg-faint">0</span>}
                    </td>
                    <td className="text-right">
                      <Link to={`/patients/${p.id}/live`} className="text-xs font-medium text-teal-text hover:underline" onClick={(e) => e.stopPropagation()} aria-label={`Open live console for ${p.name}`}>
                        Live console
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
      {creating && <CreatePatientDialog onClose={() => setCreating(false)} />}
    </div>
  );
}

function CreatePatientDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [f, setF] = useState<PatientCreate>({ name: '', mrn: '', dob: '', sex: '', notes: '', consent: false });
  const m = useMutation({
    mutationFn: () => api.createPatient({
      name: f.name.trim(), consent: f.consent,
      ...(f.mrn ? { mrn: f.mrn.trim() } : {}), ...(f.dob ? { dob: f.dob } : {}), ...(f.sex ? { sex: f.sex } : {}), ...(f.notes ? { notes: f.notes } : {}),
    }),
    onSuccess: (p) => {
      void qc.invalidateQueries({ queryKey: ['patients'] });
      toast.ok('Patient created', p.name);
      onClose();
      nav(`/patients/${p.id}/live`);
    },
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (f.consent && f.name.trim()) m.mutate(); };
  return (
    <Dialog open onClose={onClose} title="New patient"
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form="create-patient" busy={m.isPending} disabled={!f.consent || !f.name.trim()}>Create patient</Button>
      </>}
    >
      <form id="create-patient" onSubmit={submit} className="space-y-3">
        {m.isError && <Banner tone="critical" title="Could not create the patient.">{errorText(m.error)}</Banner>}
        <Field label="Full name">{(id) => <input id={id} className="input" required autoFocus value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />}</Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="MRN (optional)">{(id) => <input id={id} className="input" value={f.mrn} onChange={(e) => setF({ ...f, mrn: e.target.value })} />}</Field>
          <Field label="Date of birth (optional)">{(id) => <input id={id} className="input" type="date" value={f.dob} onChange={(e) => setF({ ...f, dob: e.target.value })} />}</Field>
        </div>
        <Field label="Sex (optional)">
          {(id) => (
            <Select id={id} value={f.sex} onChange={(e) => setF({ ...f, sex: e.target.value })}>
              <option value="">Not recorded</option><option value="F">Female</option><option value="M">Male</option><option value="O">Other</option>
            </Select>
          )}
        </Field>
        <Field label="Clinical notes (optional)">{(id) => <textarea id={id} className="input min-h-[4rem]" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} />}</Field>
        <label className="flex items-start gap-2 rounded border border-line bg-ink-900 p-2.5 text-sm">
          <input type="checkbox" className="mt-0.5 h-4 w-4 accent-[#00A896]" checked={f.consent} onChange={(e) => setF({ ...f, consent: e.target.checked })} />
          <span>
            <span className="font-medium text-fg">The patient has given informed consent</span>
            <span className="block text-xs text-fg-muted">Consent to ECG recording and processing is required before a device can stream for this patient.</span>
          </span>
        </label>
      </form>
    </Dialog>
  );
}
