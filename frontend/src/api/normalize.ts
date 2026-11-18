/**
 * Tolerant readers for response shapes the contract does not pin down exactly. Pure functions.
 */
import type {
  AuditEntry, Beat, BeatLabel, DemoRecord, Device, LastEvent, RulesConfig, SessionEvent, Tier, TrendGap, Trends,
} from './types';

/** Accepts a bare array or an envelope such as `{items: [...]}` / `{<key>: [...]}`. */
export function asList<T>(body: unknown, ...keys: string[]): T[] {
  if (Array.isArray(body)) return body as T[];
  if (body && typeof body === 'object') {
    const o = body as Record<string, unknown>;
    for (const k of [...keys, 'items', 'results', 'data']) if (Array.isArray(o[k])) return o[k] as T[];
  }
  return [];
}

const LABELS: Record<string, BeatLabel> = { N: 'N', S: 'S', V: 'V', F: 'F', Unknown: 'Unknown', UNKNOWN: 'Unknown', unknown: 'Unknown', '?': 'Unknown', Q: 'Unknown' };
export function normLabel(x: unknown): BeatLabel {
  return (typeof x === 'string' && LABELS[x]) || 'Unknown';
}
export function normTruth(x: unknown): BeatLabel | null {
  return x === null || x === undefined || x === '' ? null : normLabel(x);
}

const num = (x: unknown, d = 0): number => (typeof x === 'number' && Number.isFinite(x) ? x : d);

/** Fill in anything a beat may be missing so the renderers never see undefined. `fs` derives a missing field. */
export function normBeat(b: Partial<Beat> & Record<string, unknown>, fs?: number): Beat {
  const p = (b.probs ?? {}) as Partial<Record<string, number>>;
  const sample = typeof b.sample === 'number' ? b.sample : fs ? Math.round(num(b.time_s) * fs) : 0;
  const time_s = typeof b.time_s === 'number' ? b.time_s : fs ? sample / fs : 0;
  return {
    sample,
    time_s,
    label: normLabel(b.label),
    confidence: num(b.confidence),
    probs: { N: num(p.N), S: num(p.S), V: num(p.V), F: num(p.F) },
    model_version: typeof b.model_version === 'string' ? b.model_version : '',
    truth: normTruth(b.truth),
  };
}

export function normTier(x: unknown): Tier | null {
  return x === 'green' || x === 'amber' || x === 'orange' || x === 'red' ? x : null;
}

export function lastEventText(e: LastEvent | undefined): { text: string; tier: Tier | null; time: string | null } | null {
  if (!e) return null;
  if (typeof e === 'string') return { text: e, tier: null, time: null };
  const text = e.label ?? e.title ?? e.rule_id ?? '';
  if (!text) return null;
  return { text, tier: normTier(e.tier ?? e.severity), time: e.time ?? e.at ?? e.created_at ?? null };
}

export function normTrends(body: unknown): Trends {
  const o = (body && typeof body === 'object' ? body : {}) as Record<string, unknown>;
  const gapsRaw = asList<Record<string, unknown>>(o.gaps);
  const gaps: TrendGap[] = [];
  for (const g of gapsRaw) {
    const start = g.start ?? g.from ?? g.t0 ?? g.start_t;
    const end = g.end ?? g.to ?? g.t1 ?? g.end_t;
    if (typeof start === 'string' && typeof end === 'string') gaps.push({ start, end });
  }
  return {
    range: o.range as Trends['range'],
    bucket_s: typeof o.bucket_s === 'number' ? o.bucket_s : undefined,
    from: typeof o.from === 'string' ? o.from : undefined,
    to: typeof o.to === 'string' ? o.to : undefined,
    buckets: asList(Array.isArray(body) ? body : o.buckets, 'buckets'),
    gaps,
  };
}

export function normDemoRecords(body: unknown): DemoRecord[] {
  return asList<unknown>(body, 'records').map((r) => {
    if (typeof r === 'string' || typeof r === 'number') return { record: String(r), dataset: 'MIT-BIH' };
    const o = r as Record<string, unknown>;
    return {
      record: String(o.record ?? o.id ?? o.name ?? ''),
      dataset: typeof o.dataset === 'string' ? o.dataset : 'MIT-BIH',
      description: (o.description ?? o.label ?? o.note ?? null) as string | null,
      duration_s: typeof o.duration_s === 'number' ? o.duration_s : null,
      fs: typeof o.fs === 'number' ? o.fs : null,
    };
  }).filter((r) => r.record);
}

export function normDevice(d: Device & Record<string, unknown>): Device {
  return { ...d, device_id: String(d.device_id ?? d.id ?? '') };
}

export function normRules(body: unknown): RulesConfig {
  const o = (body && typeof body === 'object' ? body : {}) as Record<string, unknown>;
  const thresholds = (o.thresholds ?? o.config ?? o.rules ?? {}) as Record<string, unknown>;
  return {
    version: String(o.version ?? o.rule_version ?? ''),
    thresholds: thresholds && typeof thresholds === 'object' ? thresholds : {},
    updated_at: (o.updated_at as string | undefined) ?? null,
    updated_by: (o.updated_by as string | undefined) ?? null,
  };
}

export const auditTime = (a: AuditEntry) => a.at ?? a.created_at ?? a.time ?? null;
export const auditActor = (a: AuditEntry) => a.actor_email ?? a.actor ?? a.actor_id ?? '—';
export const auditTarget = (a: AuditEntry) => {
  const type = a.target_type ?? a.entity ?? '';
  const id = a.target_id ?? a.entity_id ?? '';
  return a.target ?? ([type, id].filter(Boolean).join(' ') || '—');
};

export interface FlatEvent {
  key: string; kind: string; label: string; tier: Tier | null; start_s: number | null; end_s: number | null;
  detail: string; alertId: string | null; ruleId: string | null;
}
/** Flatten session events: a window with findings becomes one row per finding. */
export function flattenEvents(events: SessionEvent[]): FlatEvent[] {
  const out: FlatEvent[] = [];
  events.forEach((e, i) => {
    const kind = e.kind ?? e.type ?? (e.findings ? 'window' : e.alert_id ? 'alert' : 'finding');
    if (kind === 'window' && (!Array.isArray(e.findings) || e.findings.length === 0)) return; // an uneventful window is not an event
    if (Array.isArray(e.findings) && !e.label && !e.title) {
      e.findings.forEach((f, j) => out.push({
        key: `${e.id ?? i}-${j}`, kind: 'finding', label: f.label, tier: normTier(f.severity), start_s: f.start_s ?? e.start_s ?? null,
        end_s: f.end_s ?? e.end_s ?? null, detail: f.detail ?? '', alertId: null, ruleId: f.rule_id ?? null,
      }));
      return;
    }
    out.push({
      key: String(e.id ?? i), kind, label: e.label ?? e.title ?? e.rule_id ?? kind, tier: normTier(e.tier ?? e.severity),
      start_s: e.start_s ?? e.time_s ?? null, end_s: e.end_s ?? null, detail: e.detail ?? '',
      alertId: e.alert_id ?? (kind === 'alert' ? e.id ?? null : null), ruleId: e.rule_id ?? null,
    });
  });
  return out.sort((a, b) => (a.start_s ?? 0) - (b.start_s ?? 0));
}

/**
 * Copilot citations carry evidence ids such as `A-1a2b3c4d` (kind prefix + the first 8 hex digits of the
 * record's UUID) — or, tolerated, the full record id. Find the record a citation points at.
 */
export function matchCitation<T extends { id: string }>(items: readonly T[] | undefined, citeId: string): T | undefined {
  if (!items || !citeId) return undefined;
  const exact = items.find((x) => x.id === citeId);
  if (exact) return exact;
  const short = citeId.replace(/^[A-Za-z]-/, '').replace(/-/g, '').toLowerCase();
  if (short.length < 6) return undefined;
  return items.find((x) => x.id.replace(/-/g, '').toLowerCase().startsWith(short));
}
