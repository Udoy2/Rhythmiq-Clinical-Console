import { describe, expect, it } from 'vitest';
import { safeRedirect } from './safeRedirect';

describe('safeRedirect', () => {
  it('keeps same-origin paths', () => {
    expect(safeRedirect('/alerts?x=1')).toBe('/alerts?x=1');
    expect(safeRedirect('/patients/p1/live')).toBe('/patients/p1/live');
  });
  it.each(['//evil.example', '/\\evil.example', 'https://evil.example', 'javascript:alert(1)', 'patients', '/\t/evil.example', '/a\\b', '', undefined, 42])(
    'rejects %p', (v) => { expect(safeRedirect(v)).toBe('/patients'); });
});
