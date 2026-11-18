import { create } from 'zustand';
import type { Tier } from '../api/types';

export interface Toast {
  id: string;
  tone: 'info' | 'ok' | 'error' | 'alert';
  title: string;
  body?: string;
  tier?: Tier;
  href?: string;
  hrefLabel?: string;
  /** ms; 0 = sticky until dismissed. */
  ttl: number;
}

interface ToastState {
  toasts: Toast[];
  push(t: Omit<Toast, 'id' | 'ttl'> & { ttl?: number; id?: string }): void;
  dismiss(id: string): void;
}

let seq = 0;
export const useToasts = create<ToastState>((set, get) => ({
  toasts: [],
  push: (t) => {
    const id = t.id ?? `t${++seq}`;
    if (get().toasts.some((x) => x.id === id)) return;
    // Clinical alert toasts stay until dismissed for orange/red; everything else times out.
    const ttl = t.ttl ?? (t.tone === 'alert' ? (t.tier === 'red' || t.tier === 'orange' ? 0 : 12_000) : t.tone === 'error' ? 8000 : 4000);
    set((s) => ({ toasts: [...s.toasts.slice(-4), { ...t, id, ttl }] }));
    if (ttl > 0) setTimeout(() => get().dismiss(id), ttl);
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

export const toast = {
  ok: (title: string, body?: string) => useToasts.getState().push({ tone: 'ok', title, body }),
  info: (title: string, body?: string) => useToasts.getState().push({ tone: 'info', title, body }),
  error: (title: string, body?: string) => useToasts.getState().push({ tone: 'error', title, body }),
};
