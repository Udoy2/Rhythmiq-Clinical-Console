export function fmtNum(x: number | null | undefined, digits = 0, dash = '—'): string {
  return typeof x === 'number' && Number.isFinite(x) ? x.toFixed(digits) : dash;
}
export function fmtPct(x: number | null | undefined, digits = 1): string {
  return typeof x === 'number' && Number.isFinite(x) ? `${x.toFixed(digits)}%` : '—';
}
export function parseDate(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}
const DT = new Intl.DateTimeFormat(undefined, { year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
const DTS = new Intl.DateTimeFormat(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
const TM = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
const D = new Intl.DateTimeFormat(undefined, { year: 'numeric', month: 'short', day: '2-digit' });
export const fmtDateTime = (iso: string | null | undefined) => { const d = parseDate(iso); return d ? DT.format(d) : '—'; };
export const fmtDateTimeS = (iso: string | null | undefined) => { const d = parseDate(iso); return d ? DTS.format(d) : '—'; };
export const fmtTime = (iso: string | null | undefined) => { const d = parseDate(iso); return d ? TM.format(d) : '—'; };
export const fmtDate = (iso: string | null | undefined) => { const d = parseDate(iso); return d ? D.format(d) : '—'; };

export function fmtAgo(iso: string | null | undefined, now = Date.now()): string {
  const d = parseDate(iso);
  if (!d) return '—';
  const s = Math.max(0, Math.round((now - d.getTime()) / 1000));
  if (s < 5) return 'just now';
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}
export function fmtDuration(sec: number | null | undefined): string {
  if (typeof sec !== 'number' || !Number.isFinite(sec)) return '—';
  const s = Math.round(sec);
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`;
  return `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')} min`;
}
export function ageFromDob(dob: string | null | undefined): number | null {
  const d = parseDate(dob);
  if (!d) return null;
  const n = new Date();
  let a = n.getFullYear() - d.getFullYear();
  if (n.getMonth() < d.getMonth() || (n.getMonth() === d.getMonth() && n.getDate() < d.getDate())) a--;
  return a;
}
export const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(' ');

/** "serial" → device, "replay" → replay; anything else passes through. */
export function humanKey(k: string): string {
  return k.replace(/_/g, ' ').replace(/\bpct\b/g, '%').replace(/\bbpm\b/g, 'bpm').replace(/^\w/, (c) => c.toUpperCase());
}
