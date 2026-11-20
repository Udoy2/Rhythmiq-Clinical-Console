import { buildUrl, download, freshAccessToken, refreshTokens, request } from './client';
import { asList, normBeat, normDemoRecords, normDevice, normRules, normTrends } from './normalize';
import { transport } from './transport';
import type {
  Alert, AlertState, AuditEntry, Beat, CopilotRequest, DemoRecord, DemoReplayRequest, DemoReplayResponse, Device,
  Health, Insight, LoginRequest, LoginResponse, Meta, PairResponse, Patient, PatientCreate, PatientNote, Role,
  RulesConfig, Session, SessionEvent, SignalChunk, Tier, TrendRange, Trends, User,
} from './types';

export const api = {
  // auth
  login: (body: LoginRequest) => request<LoginResponse>('POST', '/auth/login', { body, auth: false }),
  logout: (refresh_token: string | null) => request<void>('POST', '/auth/logout', { body: { refresh_token } }),
  me: () => request<User>('GET', '/auth/me'),
  mfaEnrol: () => request<{ secret?: string; otpauth_url?: string; uri?: string; provisioning_uri?: string }>('POST', '/auth/mfa/enrol'),
  mfaVerify: (otp: string) => request<unknown>('POST', '/auth/mfa/verify', { body: { otp } }),

  // patients
  patients: async (q?: string) => asList<Patient>(await request<unknown>('GET', '/patients', { query: { q } }), 'patients'),
  patient: (id: string) => request<Patient>('GET', `/patients/${id}`),
  createPatient: (body: PatientCreate) => request<Patient>('POST', '/patients', { body }),
  updatePatient: (id: string, body: Partial<PatientCreate>) => request<Patient>('PATCH', `/patients/${id}`, { body }),
  notes: async (id: string) => asList<PatientNote>(await request<unknown>('GET', `/patients/${id}/notes`), 'notes'),
  addNote: (id: string, body: { text: string; kind?: string }) => request<PatientNote>('POST', `/patients/${id}/notes`, { body }),

  // devices
  devices: async () => asList<Device & Record<string, unknown>>(await request<unknown>('GET', '/devices'), 'devices').map(normDevice),
  createDevice: async (body: { patient_id?: string; name?: string; device_id?: string }) =>
    normDevice(await request<Device & Record<string, unknown>>('POST', '/devices', { body })),
  pairDevice: (body: { pairing_code: string; device_id?: string }) => request<PairResponse>('POST', '/devices/pair', { body }),

  // sessions
  sessions: async (patientId: string) => asList<Session>(await request<unknown>('GET', `/patients/${patientId}/sessions`), 'sessions'),
  session: (id: string) => request<Session>('GET', `/sessions/${id}`),
  sessionBeats: async (id: string, from?: number, to?: number, fs?: number): Promise<Beat[]> =>
    asList<Record<string, unknown>>(await request<unknown>('GET', `/sessions/${id}/beats`, { query: { from, to } }), 'beats')
      .map((b) => normBeat(b, fs)),
  sessionEvents: async (id: string, findingsOnly = false) =>
    asList<SessionEvent>(await request<unknown>('GET', `/sessions/${id}/events`, { query: findingsOnly ? { findings_only: true } : undefined }), 'events'),
  sessionSignal: (id: string, from?: number, to?: number) =>
    request<SignalChunk>('GET', `/sessions/${id}/signal`, { query: { from, to } }),
  sessionCsv: (id: string) => download(`/sessions/${id}/export.csv`),

  // trends
  trends: async (patientId: string, range: TrendRange): Promise<Trends> =>
    normTrends(await request<unknown>('GET', `/patients/${patientId}/trends`, { query: { range } })),

  // alerts
  alerts: async (f: { state?: AlertState | ''; tier?: Tier | ''; patient_id?: string; evidence?: boolean } = {}) =>
    asList<Alert>(await request<unknown>('GET', '/alerts', { query: f }), 'alerts'),
  alert: (id: string) => request<Alert>('GET', `/alerts/${id}`),
  alertAction: (id: string, action: 'ack' | 'escalate' | 'close', note: string) =>
    request<Alert>('POST', `/alerts/${id}/${action}`, { body: { note: note || null } }),

  // copilot
  copilot: (body: CopilotRequest) => request<Insight>('POST', '/copilot/query', { body }),
  insights: async (patientId: string) => asList<Insight>(await request<unknown>('GET', `/patients/${patientId}/insights`), 'insights'),

  // Dr. Rhidoy guide
  assistantSuggestions: (page?: string) =>
    request<{ id: string; label: string }[]>('GET', '/assistant/suggestions', { query: { page } }),
  assistantChatStream: async (
    body: { message: string; page?: string; history?: { role: string; content: string }[] },
    handlers: { onToken: (text: string) => void; onReplace?: (text: string) => void; onDone?: (source?: string) => void },
  ) => {
    const run = async (retry: boolean): Promise<void> => {
      const token = await freshAccessToken();
      const res = await transport.fetch(buildUrl('/assistant/chat'), {
        method: 'POST',
        headers: {
          Accept: 'text/event-stream',
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(body),
      });
      if (res.status === 401 && retry) {
        if (await refreshTokens()) return run(false);
      }
      if (!res.ok) {
        let detail = `Request failed (${res.status})`;
        try {
          const j = (await res.json()) as { detail?: string };
          if (typeof j.detail === 'string') detail = j.detail;
        } catch { /* ignore */ }
        throw new Error(detail);
      }
      if (!res.body) throw new Error('No response stream');
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop() ?? '';
        for (const part of parts) {
          const line = part.split('\n').map((l) => l.trim()).find((l) => l.startsWith('data:'));
          if (!line) continue;
          try {
            const payload = JSON.parse(line.slice(5).trim()) as { type: string; text?: string; source?: string };
            if (payload.type === 'token' && payload.text) handlers.onToken(payload.text);
            if (payload.type === 'replace' && payload.text) handlers.onReplace?.(payload.text);
            if (payload.type === 'done') handlers.onDone?.(payload.source);
          } catch { /* partial JSON */ }
        }
      }
    };
    await run(true);
  },

  // reports
  reportPdf: (patientId: string, range: TrendRange) => download(`/reports/${patientId}.pdf`, { range }),

  // demo
  demoRecords: async (): Promise<DemoRecord[]> => normDemoRecords(await request<unknown>('GET', '/demo/records')),
  demoReplay: (body: DemoReplayRequest) => request<DemoReplayResponse>('POST', '/demo/replay', { body }),
  demoStop: (patient_id?: string) => request<unknown>('POST', '/demo/stop', { body: { patient_id } }),

  // admin
  audit: async (f: { actor?: string; from?: string; to?: string; action?: string }) =>
    asList<AuditEntry>(await request<unknown>('GET', '/audit', { query: f }), 'entries', 'audit'),
  users: async () => asList<User>(await request<unknown>('GET', '/admin/users'), 'users'),
  updateUser: (id: string, body: { role?: Role; active?: boolean }) => request<User>('PATCH', `/admin/users/${id}`, { body }),
  rules: async (): Promise<RulesConfig> => normRules(await request<unknown>('GET', '/admin/rules')),
  putRules: async (thresholds: Record<string, unknown>): Promise<RulesConfig> =>
    normRules(await request<unknown>('PUT', '/admin/rules', { body: { thresholds } })),

  // meta
  health: () => request<Health>('GET', '/health', { auth: false }),
  meta: () => request<Meta>('GET', '/meta'),
};

export const qk = {
  me: ['me'] as const,
  patients: (q?: string) => ['patients', q ?? ''] as const,
  patient: (id: string) => ['patient', id] as const,
  notes: (id: string) => ['patient', id, 'notes'] as const,
  sessions: (id: string) => ['patient', id, 'sessions'] as const,
  session: (id: string) => ['session', id] as const,
  sessionEvents: (id: string) => ['session', id, 'events'] as const,
  trends: (id: string, r: TrendRange) => ['patient', id, 'trends', r] as const,
  alerts: (f: object) => ['alerts', f] as const,
  alert: (id: string) => ['alert', id] as const,
  insights: (id: string) => ['patient', id, 'insights'] as const,
  demoRecords: ['demo', 'records'] as const,
  devices: ['devices'] as const,
  users: ['admin', 'users'] as const,
  rules: ['admin', 'rules'] as const,
  audit: (f: object) => ['audit', f] as const,
  meta: ['meta'] as const,
};
