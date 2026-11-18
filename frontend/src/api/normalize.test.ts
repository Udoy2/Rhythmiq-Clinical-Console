import { describe, expect, it } from 'vitest';
import { asList, flattenEvents, lastEventText, matchCitation, normBeat, normDemoRecords, normRules, normTrends } from './normalize';

describe('tolerant readers', () => {
  it('asList accepts arrays and envelopes', () => {
    expect(asList([1, 2])).toEqual([1, 2]);
    expect(asList({ items: [1] })).toEqual([1]);
    expect(asList({ beats: [3] }, 'beats')).toEqual([3]);
    expect(asList(null)).toEqual([]);
  });

  it('normBeat fills gaps and maps unknown labels', () => {
    const b = normBeat({ sample: 720, label: 'Q', confidence: 0.4 } as never, 360);
    expect(b).toMatchObject({ sample: 720, time_s: 2, label: 'Unknown', truth: null, probs: { N: 0, S: 0, V: 0, F: 0 } });
    expect(normBeat({ time_s: 1.5, label: 'V', truth: 'V' }, 360).sample).toBe(540);
  });

  it('reads last_event as a string or an object', () => {
    expect(lastEventText('V couplet')).toEqual({ text: 'V couplet', tier: null, time: null });
    expect(lastEventText({ label: 'Non-sustained V run', tier: 'orange', time: 't' })).toEqual({ text: 'Non-sustained V run', tier: 'orange', time: 't' });
    expect(lastEventText(null)).toBeNull();
  });

  it('normTrends keeps buckets, gaps (start/end or from/to) and the range bounds', () => {
    const t = normTrends({ range: '7d', from: 'a', to: 'b', buckets: [{ t: 'x' }], gaps: [{ start: 's', end: 'e' }, { from: 'f', to: 'g' }, { bad: 1 }] });
    expect(t.buckets.length).toBe(1);
    expect(t.gaps).toEqual([{ start: 's', end: 'e' }, { start: 'f', end: 'g' }]);
    expect(t.from).toBe('a');
    expect(normTrends(null)).toMatchObject({ buckets: [], gaps: [] });
  });

  it('normDemoRecords accepts strings and objects', () => {
    expect(normDemoRecords(['208', { record: '100', dataset: 'MIT-BIH', description: 'Mostly normal' }])).toMatchObject([
      { record: '208', dataset: 'MIT-BIH' }, { record: '100', description: 'Mostly normal' },
    ]);
  });

  it('normRules reads thresholds or config and a numeric version', () => {
    expect(normRules({ version: 3, config: { pause_s: 2 } })).toMatchObject({ version: '3', thresholds: { pause_s: 2 } });
  });
});

describe('flattenEvents', () => {
  it('expands windows into findings, drops uneventful windows and keeps alerts, gaps and notes in time order', () => {
    const f = { rule_id: 'V_COUPLET', label: 'V couplet', severity: 'amber' as const, start_s: 14.2, end_s: 15, value: 2, detail: '2 consecutive V beats' };
    const ev = flattenEvents([
      { kind: 'window', id: 'w1', start_s: 0, end_s: 10, findings: [] },
      { kind: 'window', id: 'w2', start_s: 10, end_s: 20, findings: [f] },
      { kind: 'alert', id: 'a1', alert_id: 'a1', title: 'Non-sustained V run', tier: 'orange', start_s: 30, end_s: 40 },
      { kind: 'gap', id: 'gap-1', label: 'Signal gap', start_s: 5, end_s: 6.5, detail: '1.50 s of signal missing' },
    ]);
    expect(ev.map((e) => e.kind)).toEqual(['gap', 'finding', 'alert']);
    expect(ev[1]).toMatchObject({ label: 'V couplet', tier: 'amber', start_s: 14.2, ruleId: 'V_COUPLET' });
    expect(ev[2]).toMatchObject({ alertId: 'a1', tier: 'orange' });
  });
});

describe('matchCitation', () => {
  const alerts = [{ id: '1a2b3c4d-0000-4000-8000-000000000001' }, { id: 'ffff0000-0000-4000-8000-000000000002' }];
  it('resolves short evidence ids and full ids', () => {
    expect(matchCitation(alerts, 'A-1a2b3c4d')).toBe(alerts[0]);
    expect(matchCitation(alerts, 'ffff0000-0000-4000-8000-000000000002')).toBe(alerts[1]);
    expect(matchCitation(alerts, 'A-deadbeef')).toBeUndefined();
    expect(matchCitation(alerts, 'T-recent')).toBeUndefined();
    expect(matchCitation(undefined, 'A-1a2b3c4d')).toBeUndefined();
  });
});
