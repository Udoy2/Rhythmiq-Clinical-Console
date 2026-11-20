import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { errorText } from '../api/client';
import { api, qk } from '../api/endpoints';
import type { Insight } from '../api/types';
import { InsightBody } from '../components/InsightCard';
import { Banner, Button, Card, EmptyState, QueryState } from '../components/ui';
import { canUseCopilot, useAuth } from '../state/auth';
import { usePatient } from './PatientLayout';

const SUGGESTIONS = ['What changed since yesterday?', 'How does the latest session compare with earlier ones?', 'Which alerts are still open and why did they fire?'];

export default function InsightsPage() {
  const { patient } = usePatient();
  const role = useAuth((s) => s.user?.role);
  const qc = useQueryClient();
  const [question, setQuestion] = useState('');
  const stored = useQuery({ queryKey: qk.insights(patient.id), queryFn: () => api.insights(patient.id) });
  const ask = useMutation({
    mutationFn: (q?: string) => api.copilot({ patient_id: patient.id, ...(q ? { question: q } : {}) }),
    onSuccess: (ins) => {
      qc.setQueryData<Insight[]>(qk.insights(patient.id), (old) => [ins, ...(old ?? []).filter((x) => x.id !== ins.id)]);
      setQuestion('');
    },
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (!ask.isPending) ask.mutate(question.trim() || undefined); };

  if (!canUseCopilot(role)) {
    return <Card title="AI insights"><EmptyState title="Not available for your role">The copilot is available to clinicians.</EmptyState></Card>;
  }

  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_22rem]">
      <div className="min-w-0 space-y-3">
        <Card title="Ask the copilot">
          <form onSubmit={submit} className="space-y-2">
            <label htmlFor="q" className="label">Follow-up question (optional — leave empty for a general summary)</label>
            <div className="flex gap-2">
              <input id="q" className="input" value={question} maxLength={300} onChange={(e) => setQuestion(e.target.value)} placeholder="What changed since yesterday?" />
              <Button type="submit" variant="primary" busy={ask.isPending}>{question.trim() ? 'Ask' : 'Summarise'}</Button>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {SUGGESTIONS.map((s) => (
                <button key={s} type="button" onClick={() => setQuestion(s)} className="rounded border border-line px-2 py-0.5 text-xs text-fg-muted hover:border-teal hover:text-fg">{s}</button>
              ))}
            </div>
          </form>
          {ask.isError && <div className="mt-2"><Banner tone="critical" title="The copilot request failed.">{errorText(ask.error)}</Banner></div>}
        </Card>

        <QueryState query={stored} loadingLabel="Loading insights…"
          empty={<Card title="Insights"><EmptyState title="No insights yet">Ask a question or request a summary. Every statement will cite the record it comes from.</EmptyState></Card>}>
          {(list) => (
            <ul className="space-y-3" aria-label="Insights, newest first">
              {list.map((ins) => (
                <li key={ins.id} className="panel p-3"><InsightBody insight={ins} patientId={patient.id} /></li>
              ))}
            </ul>
          )}
        </QueryState>
      </div>
      <Card title="How the copilot is bounded" as="aside" className="self-start">
        <ul className="list-disc space-y-1.5 pl-4 text-sm text-fg-muted">
          <li>It reads only this patient's retrieved records: sessions, rhythm windows, alerts and notes.</li>
          <li>Every factual sentence must cite a record; sentences without a citation are removed before display.</li>
          <li>It does not diagnose, prescribe or give emergency instructions.</li>
          <li>If the language model is unavailable, a rules-based template with the same citations is shown and labelled TEMPLATE.</li>
          <li>Numbered references open the cited alert or the cited moment in a stored session.</li>
        </ul>
      </Card>
    </div>
  );
}
