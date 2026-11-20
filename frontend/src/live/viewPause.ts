/** Survives Live console unmount when switching patient tabs (History / Insights / …). */

export type EcgViewPause = { paused: true; head: number } | { paused: false };

const STORAGE_KEY = 'rhythmiq.ecgViewPause';

function readAll(): Record<string, EcgViewPause> {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, EcgViewPause>;
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function writeAll(all: Record<string, EcgViewPause>): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(all));
  } catch { /* private mode / quota */ }
}

export function readViewPause(patientId: string): EcgViewPause | null {
  return readAll()[patientId] ?? null;
}

export function writeViewPause(patientId: string, state: EcgViewPause): void {
  const all = readAll();
  all[patientId] = state;
  writeAll(all);
}

export function clearViewPause(patientId: string): void {
  const all = readAll();
  if (!(patientId in all)) return;
  delete all[patientId];
  writeAll(all);
}
