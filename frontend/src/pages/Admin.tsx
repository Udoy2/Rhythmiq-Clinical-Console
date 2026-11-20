import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { NavLink, useParams } from 'react-router-dom';
import { errorText } from '../api/client';
import { api, qk } from '../api/endpoints';
import { auditActor, auditTarget, auditTime } from '../api/normalize';
import { ROLES, type Device, type PairResponse, type Role } from '../api/types';
import { Badge, Banner, Button, Card, Dialog, EmptyState, Field, Mono, PageHeader, QueryState, Select } from '../components/ui';
import { cx, fmtAgo, fmtDateTimeS, humanKey } from '../lib/format';
import { isAdmin, ROLE_LABEL, useAuth } from '../state/auth';
import { toast } from '../state/toasts';

type Tab = 'users' | 'devices' | 'rules' | 'audit';

export default function AdminPage() {
  const role = useAuth((s) => s.user?.role);
  const admin = isAdmin(role);
  const { tab: raw } = useParams();
  const tabs: { id: Tab; label: string }[] = admin
    ? [{ id: 'users', label: 'Users & roles' }, { id: 'devices', label: 'Devices' }, { id: 'rules', label: 'Alert rules' }, { id: 'audit', label: 'Audit log' }]
    : [{ id: 'devices', label: 'Devices' }];
  const tab = tabs.find((t) => t.id === raw)?.id ?? tabs[0].id;

  return (
    <div>
      <PageHeader title={admin ? 'Admin' : 'Devices'} sub={admin ? 'Users, devices, alert rules and the audit trail. Changes are versioned and audited.' : 'Pair and check devices.'} />
      <nav aria-label="Admin sections" className="mb-3 flex gap-1 overflow-x-auto overflow-y-hidden border-b border-line">
        {tabs.map((t) => (
          <NavLink key={t.id} to={`/admin/${t.id}`} className={() => cx('-mb-px whitespace-nowrap border-b-2 px-3 py-1.5 text-sm', t.id === tab ? 'border-accent font-semibold text-fg' : 'border-transparent text-fg-muted hover:text-fg')}
            aria-current={t.id === tab ? 'page' : undefined}>
            {t.label}
          </NavLink>
        ))}
      </nav>
      {tab === 'users' && <UsersTab />}
      {tab === 'devices' && <DevicesTab />}
      {tab === 'rules' && <RulesTab />}
      {tab === 'audit' && <AuditTab />}
    </div>
  );
}

// ───────────── Users ─────────────
function UsersTab() {
  const me = useAuth((s) => s.user);
  const qc = useQueryClient();
  const q = useQuery({ queryKey: qk.users, queryFn: api.users });
  const [confirm, setConfirm] = useState<{ id: string; email: string } | null>(null);
  const m = useMutation({
    mutationFn: (v: { id: string; role?: Role; active?: boolean }) => api.updateUser(v.id, { role: v.role, active: v.active }),
    onSuccess: (u) => { void qc.invalidateQueries({ queryKey: qk.users }); toast.ok('User updated', `${u.email} — ${ROLE_LABEL[u.role] ?? u.role}`); },
    onError: (e) => toast.error('Could not update the user', errorText(e)),
  });
  return (
    <Card title="Users & roles" bodyClass="p-0">
      <Dialog
        open={!!confirm}
        onClose={() => setConfirm(null)}
        title="Disable this user?"
        footer={<>
          <Button data-autofocus onClick={() => setConfirm(null)}>Cancel</Button>
          <Button variant="danger" onClick={() => { if (confirm) m.mutate({ id: confirm.id, active: false }); setConfirm(null); }}>Disable user</Button>
        </>}
      >
        <p className="text-sm"><span className="font-medium">{confirm?.email}</span> will not be able to sign in until an admin enables the account again.</p>
      </Dialog>
      <QueryState query={q} empty={<EmptyState title="No users" />}>
        {(list) => (
          <div className="overflow-x-auto">
            <table>
              <thead><tr><th scope="col">User</th><th scope="col">Role</th><th scope="col">MFA</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {list.map((u) => {
                  const self = u.id === me?.id;
                  return (
                    <tr key={u.id}>
                      <td><div className="font-medium text-fg">{u.name || '—'} {self && <Badge tone="outline">YOU</Badge>}</div><Mono>{u.email}</Mono></td>
                      <td>
                        <label className="sr-only" htmlFor={`role-${u.id}`}>Role of {u.email}</label>
                        <Select id={`role-${u.id}`} className="!w-40" value={u.role} disabled={self || m.isPending}
                          onChange={(e) => m.mutate({ id: u.id, role: e.target.value as Role })}>
                          {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
                        </Select>
                      </td>
                      <td>{u.mfa_enabled ? <span className="text-accent-text">Enabled</span> : <span className="text-fg-muted">Not enrolled</span>}</td>
                      <td>{u.active === false ? <span className="text-amber-text">Disabled</span> : <span className="text-fg">Active</span>}</td>
                      <td className="text-right">
                        {u.active !== undefined && !self && (
                          <Button size="sm" disabled={m.isPending} onClick={() => (u.active ? setConfirm({ id: u.id, email: u.email }) : m.mutate({ id: u.id, active: true }))}>{u.active ? 'Disable' : 'Enable'}</Button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </QueryState>
    </Card>
  );
}

// ───────────── Devices + pairing ─────────────
function Countdown({ until }: { until: string }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  const left = Math.max(0, Math.round((new Date(until).getTime() - now) / 1000));
  if (!Number.isFinite(left)) return null;
  return left > 0
    ? <span className="num text-fg-muted">expires in {Math.floor(left / 60)}:{String(left % 60).padStart(2, '0')}</span>
    : <span className="font-semibold text-alert-text">expired — create a new code</span>;
}

function DevicesTab() {
  const qc = useQueryClient();
  const devices = useQuery({ queryKey: qk.devices, queryFn: api.devices, refetchInterval: 15_000 });
  const patients = useQuery({ queryKey: qk.patients(), queryFn: () => api.patients() });
  const [patientId, setPatientId] = useState('');
  const [name, setName] = useState('');
  const [issued, setIssued] = useState<Device | null>(null);
  const [code, setCode] = useState('');
  const [paired, setPaired] = useState<PairResponse | null>(null);

  const create = useMutation({
    mutationFn: () => api.createDevice({ ...(patientId ? { patient_id: patientId } : {}), ...(name.trim() ? { name: name.trim() } : {}) }),
    onSuccess: (d) => { setIssued(d); setPaired(null); if (d.pairing_code) setCode(d.pairing_code); void qc.invalidateQueries({ queryKey: qk.devices }); },
  });
  const pair = useMutation({
    mutationFn: () => api.pairDevice({ pairing_code: code.trim(), ...(issued?.device_id ? { device_id: issued.device_id } : {}) }),
    onSuccess: (r) => { setPaired(r); setIssued(null); setCode(''); void qc.invalidateQueries({ queryKey: qk.devices }); },
  });
  const consented = (patients.data ?? []).filter((p) => p.consent);
  const expiry = issued?.pairing_expires_at ?? issued?.expires_at ?? null;

  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_24rem]">
      <Card title="Devices" bodyClass="p-0">
        <QueryState query={devices} empty={<EmptyState title="No devices registered">Create a pairing code to add the first device.</EmptyState>}>
          {(list) => (
            <div className="overflow-x-auto">
              <table>
                <thead><tr><th scope="col">Device</th><th scope="col">Patient</th><th scope="col">Firmware</th><th scope="col">Pairing</th><th scope="col">Signal quality</th><th scope="col">Last seen</th></tr></thead>
                <tbody>
                  {list.map((d) => {
                    const pn = d.patient_name ?? patients.data?.find((p) => p.id === d.patient_id)?.name;
                    const sq = d.signal_quality ?? d.sqi;
                    return (
                      <tr key={d.device_id}>
                        <td><div className="font-medium text-fg">{d.name ?? d.device_id}</div><Mono>{d.device_id}</Mono></td>
                        <td>{pn ?? <span className="text-fg-faint">Unassigned</span>}</td>
                        <td><Mono>{d.firmware ?? '—'}</Mono></td>
                        <td>{d.paired === false ? <span className="text-amber-text">Waiting for pairing</span> : 'Paired'}</td>
                        <td className="num">{typeof sq === 'number' ? sq.toFixed(2) : '—'}{d.status ? <span className="ml-2 font-sans text-xs text-fg-muted">{d.status}</span> : null}</td>
                        <td className="num whitespace-nowrap text-fg-muted">{d.last_seen ? fmtAgo(d.last_seen) : 'never'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </QueryState>
      </Card>

      <div className="space-y-3">
        <Card title="1 · Create a pairing code">
          <form onSubmit={(e: FormEvent) => { e.preventDefault(); create.mutate(); }} className="space-y-2">
            <Field label="Patient" hint="Only patients with recorded consent can be paired.">
              {(id, desc) => (
                <Select id={id} aria-describedby={desc} value={patientId} onChange={(e) => setPatientId(e.target.value)}>
                  <option value="">Not assigned yet</option>
                  {consented.map((p) => <option key={p.id} value={p.id}>{p.name}{p.mrn ? ` (${p.mrn})` : ''}</option>)}
                </Select>
              )}
            </Field>
            <Field label="Device name (optional)">{(id) => <input id={id} className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="AD8232 kit 02" />}</Field>
            {create.isError && <Banner tone="critical" title="Could not create the device.">{errorText(create.error)}</Banner>}
            <Button type="submit" variant="primary" busy={create.isPending}>Create pairing code</Button>
          </form>
          {issued && (
            <div className="mt-3 rounded border border-accent/50 bg-accent-dim/30 p-3" role="status">
              <div className="label">Pairing code for <Mono className="text-fg">{issued.device_id}</Mono></div>
              {issued.pairing_code
                ? <div className="num mt-1 text-3xl font-semibold tracking-[0.25em] text-fg">{issued.pairing_code}</div>
                : <p className="mt-1 text-sm text-fg-muted">The service did not return a code for this device.</p>}
              {expiry && <div className="mt-1 text-xs"><Countdown until={expiry} /></div>}
              <p className="mt-1 text-xs text-fg-muted">Codes are single-use and expire after 10 minutes.</p>
            </div>
          )}
        </Card>

        <Card title="2 · Complete pairing">
          <form onSubmit={(e: FormEvent) => { e.preventDefault(); if (code.trim()) pair.mutate(); }} className="space-y-2">
            <Field label="Pairing code">
              {(id) => <input id={id} className="input num tracking-[0.2em]" value={code} onChange={(e) => setCode(e.target.value)} autoComplete="off" required />}
            </Field>
            {pair.isError && <Banner tone="critical" title="Pairing failed.">{errorText(pair.error)}</Banner>}
            <Button type="submit" busy={pair.isPending} disabled={!code.trim()}>Pair device</Button>
          </form>
          {paired && (
            <div className="mt-3 rounded border border-amber/60 bg-amber-dim p-3" role="status">
              <div className="text-sm font-semibold text-fg">Device paired — copy the key now</div>
              <dl className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
                <dt className="text-fg-muted">Device ID</dt><dd><Mono className="text-fg">{paired.device_id}</Mono></dd>
                <dt className="text-fg-muted">Device key</dt><dd><Mono className="text-fg">{paired.device_key}</Mono></dd>
              </dl>
              <p className="mt-1.5 text-xs text-fg-muted">The key is shown once. Configure it on the device or the stream sender (<span className="font-mono">?key=…</span>).</p>
              <Button size="sm" className="mt-2" onClick={() => { void navigator.clipboard?.writeText(paired.device_key).then(() => toast.ok('Device key copied')); }}>Copy key</Button>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}

// ───────────── Rules ─────────────
type Flat = { path: string; value: unknown };
function flatten(o: Record<string, unknown>, prefix = ''): Flat[] {
  return Object.entries(o).flatMap(([k, v]) => {
    const path = prefix ? `${prefix}.${k}` : k;
    return v && typeof v === 'object' && !Array.isArray(v) ? flatten(v as Record<string, unknown>, path) : [{ path, value: v }];
  });
}
function setPath(o: Record<string, unknown>, path: string, value: unknown): Record<string, unknown> {
  const [head, ...rest] = path.split('.');
  return { ...o, [head]: rest.length ? setPath((o[head] ?? {}) as Record<string, unknown>, rest.join('.'), value) : value };
}

function RulesTab() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: qk.rules, queryFn: api.rules });
  const [draft, setDraft] = useState<Record<string, string>>({});
  useEffect(() => setDraft({}), [q.data?.version]);
  const rows = useMemo(() => (q.data ? flatten(q.data.thresholds) : []), [q.data]);

  const invalid = Object.entries(draft).filter(([path, v]) => {
    const orig = rows.find((r) => r.path === path)?.value;
    return typeof orig === 'number' && (v.trim() === '' || !Number.isFinite(Number(v)));
  }).map(([p]) => p);
  const dirty = Object.entries(draft).filter(([path, v]) => String(rows.find((r) => r.path === path)?.value) !== v);

  const save = useMutation({
    mutationFn: () => {
      let next = q.data!.thresholds;
      for (const [path, v] of dirty) {
        const orig = rows.find((r) => r.path === path)?.value;
        next = setPath(next, path, typeof orig === 'number' ? Number(v) : typeof orig === 'boolean' ? v === 'true' : v);
      }
      return api.putRules(next);
    },
    onSuccess: (r) => { qc.setQueryData(qk.rules, r); setDraft({}); void qc.invalidateQueries({ queryKey: qk.meta }); toast.ok('Rule thresholds saved', `New version ${r.version}`); },
  });

  return (
    <Card title="Alert rule thresholds" right={q.data && <span className="text-xs text-fg-muted">version <Mono className="text-fg">{q.data.version || '—'}</Mono></span>}>
      <QueryState query={q} isEmpty={(r) => Object.keys(r.thresholds).length === 0} empty={<EmptyState title="The service reported no configurable thresholds" />}>
        {(r) => (
          <form onSubmit={(e: FormEvent) => { e.preventDefault(); if (dirty.length && !invalid.length) save.mutate(); }}>
            <p className="mb-3 text-sm text-fg-muted">
              Saving creates a new rule version. Every rhythm window and alert records the version that produced it.
              {r.updated_at && <> Last changed <span className="num">{fmtDateTimeS(r.updated_at)}</span>{r.updated_by ? ` by ${r.updated_by}` : ''}.</>}
            </p>
            <div className="grid grid-cols-1 gap-x-6 gap-y-2 md:grid-cols-2 xl:grid-cols-3">
              {rows.map(({ path, value }) => {
                const editable = typeof value === 'number' || typeof value === 'string' || typeof value === 'boolean';
                const cur = draft[path] ?? String(value);
                const changed = draft[path] !== undefined && draft[path] !== String(value);
                const bad = invalid.includes(path);
                return (
                  <div key={path} className="flex items-center justify-between gap-3 border-b border-line/50 py-1">
                    <label htmlFor={`rule-${path}`} className="min-w-0 text-sm text-fg">
                      {humanKey(path.split('.').pop() ?? path)}
                      <span className="block truncate font-mono text-2xs text-fg-faint">{path}</span>
                    </label>
                    {typeof value === 'boolean' ? (
                      <Select id={`rule-${path}`} className="!w-24" value={cur} onChange={(e) => setDraft({ ...draft, [path]: e.target.value })}>
                        <option value="true">on</option><option value="false">off</option>
                      </Select>
                    ) : (
                      <input id={`rule-${path}`} className={cx('input num !w-28 text-right', changed && 'border-accent', bad && '!border-alert')} disabled={!editable}
                        inputMode={typeof value === 'number' ? 'decimal' : undefined} aria-invalid={bad || undefined}
                        value={editable ? cur : JSON.stringify(value)} onChange={(e) => setDraft({ ...draft, [path]: e.target.value })} />
                    )}
                  </div>
                );
              })}
            </div>
            {invalid.length > 0 && <p role="alert" className="mt-2 text-xs text-alert-text">Enter a number for: {invalid.join(', ')}</p>}
            {save.isError && <div className="mt-3"><Banner tone="critical" title="Could not save the thresholds.">{errorText(save.error)}</Banner></div>}
            <div className="mt-3 flex items-center gap-2">
              <Button type="submit" variant="primary" busy={save.isPending} disabled={dirty.length === 0 || invalid.length > 0}>Save as new version</Button>
              <Button onClick={() => setDraft({})} disabled={dirty.length === 0}>Discard changes</Button>
              <span className="text-xs text-fg-muted">{dirty.length ? `${dirty.length} unsaved change${dirty.length === 1 ? '' : 's'}` : 'No changes'}</span>
            </div>
          </form>
        )}
      </QueryState>
    </Card>
  );
}

// ───────────── Audit ─────────────
function AuditTab() {
  const [form, setForm] = useState({ actor: '', action: '', from: '', to: '' });
  const [filters, setFilters] = useState(form);
  const toIso = (d: string, end = false) => (d ? new Date(`${d}T${end ? '23:59:59' : '00:00:00'}`).toISOString() : '');
  const params = { actor: filters.actor.trim(), action: filters.action.trim(), from: toIso(filters.from), to: toIso(filters.to, true) };
  const q = useQuery({ queryKey: qk.audit(params), queryFn: () => api.audit(params) });

  return (
    <Card title="Audit log" bodyClass="p-0">
      <form onSubmit={(e: FormEvent) => { e.preventDefault(); setFilters(form); }} className="flex flex-wrap items-end gap-3 border-b border-line p-3">
        <Field label="Actor" className="w-52">{(id) => <input id={id} className="input" value={form.actor} onChange={(e) => setForm({ ...form, actor: e.target.value })} placeholder="email" />}</Field>
        <Field label="Action" className="w-44">{(id) => <input id={id} className="input" value={form.action} onChange={(e) => setForm({ ...form, action: e.target.value })} placeholder="alert.acknowledge" />}</Field>
        <Field label="From" className="w-40">{(id) => <input id={id} type="date" className="input" value={form.from} onChange={(e) => setForm({ ...form, from: e.target.value })} />}</Field>
        <Field label="To" className="w-40">{(id) => <input id={id} type="date" className="input" value={form.to} onChange={(e) => setForm({ ...form, to: e.target.value })} />}</Field>
        <Button type="submit" variant="primary">Search</Button>
        <Button onClick={() => { const z = { actor: '', action: '', from: '', to: '' }; setForm(z); setFilters(z); }}>Clear</Button>
      </form>
      <QueryState query={q} loadingLabel="Searching…" empty={<EmptyState title="No audit entries match">Adjust the filters and search again.</EmptyState>}>
        {(list) => (
          <div className="max-h-[60vh] overflow-auto">
            <table>
              <thead className="sticky top-0 bg-paper-raised"><tr><th scope="col">Time</th><th scope="col">Actor</th><th scope="col">Action</th><th scope="col">Target</th><th scope="col">Detail</th></tr></thead>
              <tbody>
                {list.map((a) => (
                  <tr key={a.id}>
                    <td className="num whitespace-nowrap">{fmtDateTimeS(auditTime(a))}</td>
                    <td>{auditActor(a)}</td>
                    <td><Mono className="text-fg">{a.action}</Mono></td>
                    <td><Mono>{auditTarget(a)}</Mono></td>
                    <td className="max-w-[24rem]"><Mono>{a.detail === null || a.detail === undefined ? '' : typeof a.detail === 'string' ? a.detail : JSON.stringify(a.detail)}</Mono></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryState>
    </Card>
  );
}
