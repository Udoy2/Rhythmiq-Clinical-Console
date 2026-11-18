import { create } from 'zustand';
import type { Role, User } from '../api/types';

const KEY = 'cardiosense.auth';

interface Persisted { accessToken: string | null; refreshToken: string | null; user: User | null }

function load(): Persisted {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (raw) return JSON.parse(raw) as Persisted;
  } catch { /* storage unavailable */ }
  return { accessToken: null, refreshToken: null, user: null };
}
function save(p: Persisted) {
  try {
    if (p.accessToken) sessionStorage.setItem(KEY, JSON.stringify(p));
    else sessionStorage.removeItem(KEY);
  } catch { /* storage unavailable */ }
}

interface AuthState extends Persisted {
  setSession(p: { accessToken: string; refreshToken: string; user?: User | null }): void;
  setUser(u: User): void;
  /** `explicit` = the user pressed Sign out (as opposed to an expired session): the next sign-in starts at the patient list. */
  clear(explicit?: boolean): void;
  explicitSignOut: boolean;
}

export const useAuth = create<AuthState>((set, get) => ({
  ...load(),
  explicitSignOut: false,
  setSession: ({ accessToken, refreshToken, user }) => {
    const next = { accessToken, refreshToken, user: user ?? get().user };
    save(next);
    set({ ...next, explicitSignOut: false });
  },
  setUser: (user) => {
    const next = { accessToken: get().accessToken, refreshToken: get().refreshToken, user };
    save(next);
    set({ user });
  },
  clear: (explicit = false) => {
    save({ accessToken: null, refreshToken: null, user: null });
    set({ accessToken: null, refreshToken: null, user: null, explicitSignOut: explicit });
  },
}));

// ── role helpers (BRD §7.3). The server is the authority; these only decide what the UI offers. ──
export const isAdmin = (r?: Role | null) => r === 'org_admin' || r === 'system_admin';
export const canManagePatients = (r?: Role | null) => r === 'clinician' || r === 'org_admin';
export const canActOnAlerts = (r?: Role | null) => r === 'clinician' || r === 'org_admin';
export const canRunDemo = (r?: Role | null) => r === 'clinician' || isAdmin(r);
export const canManageDevices = (r?: Role | null) => r === 'technician' || isAdmin(r);
export const canSeeAlerts = (r?: Role | null) => r === 'clinician' || r === 'technician' || isAdmin(r);
export const canUseCopilot = (r?: Role | null) => r === 'clinician' || r === 'org_admin';

export const ROLE_LABEL: Record<Role, string> = {
  patient: 'Patient',
  technician: 'Technician',
  clinician: 'Clinician',
  org_admin: 'Org admin',
  system_admin: 'System admin',
};
