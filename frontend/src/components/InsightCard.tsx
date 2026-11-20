import { Fragment } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api, qk } from '../api/endpoints';
import { matchCitation } from '../api/normalize';
import type { Citation, Insight } from '../api/types';
import { fmtDateTime } from '../lib/format';
import { Badge } from './ui';

/** Where a citation leads: the cited alert, the cited session, or (windows/findings) the moment in the patient history. */
export interface CiteLookup { alerts?: { id: string }[]; sessions?: { id: string }[] }

export function citationHref(c: Citation, patientId: string, lookup: CiteLookup = {}): string | null {
  const kind = (c.kind ?? '').toLowerCase();
  if (kind === 'alert') {
    const a = matchCitation(lookup.alerts, c.id);
    if (a) return `/alerts/${encodeURIComponent(a.id)}`;
  }
  if (kind === 'session') {
    const s = matchCitation(lookup.sessions, c.id);
    if (s) return `/patients/${patientId}/sessions/${encodeURIComponent(s.id)}`;
  }
  // Windows, findings and anything unresolved: open the moment in the patient's stored history.
  if (['alert', 'session', 'window', 'finding', 'event', 'episode', 'beat'].includes(kind) && c.time) {
    return `/patients/${patientId}/history?at=${encodeURIComponent(c.time)}&cite=${encodeURIComponent(c.id)}`;
  }
  return null;
}

export function CitationChip({ c, n, patientId, lookup }: { c: Citation; n: number; patientId: string; lookup?: CiteLookup }) {
  const href = citationHref(c, patientId, lookup);
  const inner = (
    <>
      <span className="num font-semibold text-accent-text">[{n}]</span>
      <span className="text-fg">{c.label || c.id}</span>
      <span className="text-fg-faint">{c.kind}{c.time ? ` · ${fmtDateTime(c.time)}` : ''}</span>
    </>
  );
  const cls = 'inline-flex max-w-full items-center gap-1.5 rounded border border-line bg-paper-sunk px-1.5 py-0.5 text-xs';
  return href
    ? <Link to={href} className={`${cls} hover:border-accent`} title={`Open cited ${c.kind} ${c.id}`}>{inner}</Link>
    : <span className={cls} title={`Evidence ${c.id}`}>{inner}</span>;
}

/** Render insight text, turning `[evidence-id]` markers into numbered references to the citation chips. */
function InsightText({ text, citations, patientId, lookup }: { text: string; citations: Citation[]; patientId: string; lookup: CiteLookup }) {
  const index = new Map(citations.map((c, i) => [c.id, i + 1]));
  const parts = text.split(/(\[[^\]\s]+\])/g);
  return (
    <p className="text-sm leading-relaxed text-fg">
      {parts.map((part, i) => {
        const m = /^\[([^\]\s]+)\]$/.exec(part);
        const n = m ? index.get(m[1]) : undefined;
        if (!m || !n) return <Fragment key={i}>{part}</Fragment>;
        const href = citationHref(citations[n - 1], patientId, lookup);
        return href
          ? <Link key={i} to={href} className="num mx-0.5 text-xs font-semibold text-accent-text hover:underline" title={citations[n - 1].label}>[{n}]</Link>
          : <span key={i} className="num mx-0.5 text-xs font-semibold text-accent-text">[{n}]</span>;
      })}
    </p>
  );
}

export function InsightBody({ insight, patientId }: { insight: Insight; patientId: string }) {
  const cites = insight.citations ?? [];
  // Citation ids are short evidence ids; resolve them against the patient's alerts and sessions (cached queries).
  const alerts = useQuery({ queryKey: qk.alerts({ patient_id: patientId, evidence: false }), queryFn: () => api.alerts({ patient_id: patientId, evidence: false }), staleTime: 30_000 });
  const sessions = useQuery({ queryKey: qk.sessions(patientId), queryFn: () => api.sessions(patientId), staleTime: 30_000 });
  const lookup: CiteLookup = { alerts: alerts.data, sessions: sessions.data };
  return (
    <article aria-label="AI insight">
      {insight.question && <p className="mb-1.5 text-xs text-fg-muted">Question: <span className="text-fg">{insight.question}</span></p>}
      <InsightText text={insight.text} citations={cites} patientId={patientId} lookup={lookup} />
      {cites.length > 0 ? (
        <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="Cited evidence">
          {cites.map((c, i) => <li key={`${c.id}-${i}`} className="max-w-full"><CitationChip c={c} n={i + 1} patientId={patientId} lookup={lookup} /></li>)}
        </ul>
      ) : (
        <p className="mt-2 text-xs text-amber-text">No evidence was cited for this text.</p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-2xs text-fg-faint">
        <Badge tone="outline" title={insight.generated_by === 'llm' ? 'Written by a language model from the cited records only' : 'Rules-based template — the language model was not used'}>
          {insight.generated_by === 'llm' ? 'LANGUAGE MODEL' : 'TEMPLATE'}
        </Badge>
        {insight.model && <span className="font-mono">{insight.model}</span>}
        <span>{fmtDateTime(insight.generated_at)}</span>
      </div>
      {insight.disclaimer && <p className="mt-1.5 text-2xs text-fg-faint">{insight.disclaimer}</p>}
    </article>
  );
}
