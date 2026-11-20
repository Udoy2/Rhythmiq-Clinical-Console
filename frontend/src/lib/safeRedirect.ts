/** Post-login target: a same-origin path only; anything else falls back to /patients. */
export function safeRedirect(from: unknown): string {
  if (typeof from !== 'string') return '/patients';
  if (!/^\/(?![/\\])/.test(from) || /[\u0000-\u001f\\]/.test(from)) return '/patients';
  return from;
}
