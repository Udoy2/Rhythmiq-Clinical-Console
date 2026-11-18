/**
 * Indirection over the two browser primitives the app talks to the backend with, so that the dev mock
 * (src/mock, enabled only with VITE_MOCK=1) can replace them without the rest of the code knowing.
 */
export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

/** The subset of the WebSocket interface the live client uses. */
export interface SocketLike {
  onopen: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: ((ev: { code?: number; reason?: string }) => void) | null;
  onerror: ((ev: unknown) => void) | null;
  close(code?: number, reason?: string): void;
}
export type SocketFactory = (url: string) => SocketLike;

let fetchImpl: FetchLike = (input, init) => fetch(input, init);
let socketFactory: SocketFactory = (url) => new WebSocket(url) as unknown as SocketLike;
let mockActive = false;

export const transport = {
  fetch: (input: string, init?: RequestInit) => fetchImpl(input, init),
  socket: (url: string) => socketFactory(url),
  get isMock() { return mockActive; },
  install(f: FetchLike, s: SocketFactory) { fetchImpl = f; socketFactory = s; mockActive = true; },
};

export const API_PREFIX = '/v1';

export function wsUrl(path: string, params: Record<string, string>): string {
  const q = new URLSearchParams(params).toString();
  const proto = typeof location !== 'undefined' && location.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = typeof location !== 'undefined' ? location.host : 'localhost';
  return `${proto}//${host}${API_PREFIX}${path}${q ? `?${q}` : ''}`;
}
