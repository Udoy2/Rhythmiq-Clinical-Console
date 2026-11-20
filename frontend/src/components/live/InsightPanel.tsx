import { useEffect, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { errorText } from '../../api/client';
import { api, qk } from '../../api/endpoints';
import type { Insight } from '../../api/types';
import { InsightBody } from '../InsightCard';
import { Button, SideSection, Spinner } from '../ui';

export function InsightPanel({ patientId }: { patientId: string }) {
  const qc = useQueryClient();
  const stored = useQuery({ queryKey: qk.insights(patientId), queryFn: () => api.insights(patientId) });
  const [question, setQuestion] = useState('');
  const ask = useMutation({
    mutationFn: (q?: string) => api.copilot({ patient_id: patientId, ...(q ? { question: q } : {}) }),
    onSuccess: (ins) => {
      qc.setQueryData<Insight[]>(qk.insights(patientId), (old) => [ins, ...(old ?? []).filter((x) => x.id !== ins.id)]);
      setQuestion('');
    },
  });

  // Drop mutation result when the patient changes so another patient's answer cannot stick.
  useEffect(() => {
    ask.reset();
    setQuestion('');
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only reset on patient switch
  }, [patientId]);

  const latest = ask.data ?? stored.data?.[0] ?? null;
  const submit = (e: FormEvent) => { e.preventDefault(); if (!ask.isPending) ask.mutate(question.trim() || undefined); };

  return (
    <SideSection title="AI insight" right={<Link to={`/patients/${patientId}/insights`} className="text-xs text-accent-text hover:underline">All insights</Link>}>
      {ask.isPending ? (
        <div className="py-3"><Spinner label="Retrieving records and writing a cited summary…" /></div>
      ) : latest ? (
        <InsightBody insight={latest} patientId={patientId} />
      ) : stored.isPending ? (
        <Spinner label="Loading…" />
      ) : (
        <p className="text-sm text-fg-muted">
          {stored.isError ? `Stored insights could not be loaded: ${errorText(stored.error)}` : 'No insight yet. Generate a cited summary of what changed for this patient.'}
        </p>
      )}
      {ask.isError && <p role="alert" className="mt-2 text-xs text-alert-text">The copilot request failed: {errorText(ask.error)}</p>}
      <form onSubmit={submit} className="mt-3 flex gap-2 border-t border-line pt-3">
        <label htmlFor="live-q" className="sr-only">Follow-up question for the copilot</label>
        <input id="live-q" className="input" placeholder="What changed since yesterday?" value={question} maxLength={300} onChange={(e) => setQuestion(e.target.value)} />
        <Button type="submit" variant="primary" busy={ask.isPending}>{question.trim() ? 'Ask' : latest ? 'Refresh' : 'Generate'}</Button>
      </form>
      <p className="mt-1.5 text-2xs text-fg-faint">Answers use only this patient's retrieved records and never give a diagnosis or treatment advice.</p>
    </SideSection>
  );
}
