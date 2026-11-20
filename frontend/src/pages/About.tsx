import type { ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, qk } from '../api/endpoints';
import type { Meta, ModelInfo } from '../api/types';
import { Banner, BeatLegend, Card, EmptyState, ErrorState, LoadingState, Mono, PageHeader } from '../components/ui';
import { humanKey } from '../lib/format';

const isNum = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x);
const isObj = (x: unknown): x is Record<string, unknown> => !!x && typeof x === 'object' && !Array.isArray(x);
const isMatrix = (x: unknown): x is number[][] => Array.isArray(x) && x.length > 0 && x.every((r) => Array.isArray(r) && r.length > 0 && r.every(isNum));
const isRows = (x: unknown): x is Record<string, unknown>[] => Array.isArray(x) && x.length > 0 && x.every(isObj);

function fmtVal(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (isNum(v)) return Number.isInteger(v) ? String(v) : Math.abs(v) < 10 ? v.toFixed(3) : v.toFixed(1);
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  if (typeof v === 'string') return v;
  if (Array.isArray(v) && v.every((x) => typeof x !== 'object')) return v.join(', ');
  return JSON.stringify(v);
}

const CLASSES = ['N', 'S', 'V', 'F'];

/** `{acc, macroF1, N_Se, N_+P, N_F1, …}` → a per-class table, when the keys have that shape. */
function perClass(o: Record<string, unknown>): { rows: { cls: string; vals: Record<string, unknown> }[]; cols: string[]; rest: [string, unknown][] } | null {
  const cols = new Set<string>();
  const by = new Map<string, Record<string, unknown>>();
  const rest: [string, unknown][] = [];
  for (const [k, v] of Object.entries(o)) {
    const m = /^([NSVF])_(.+)$/.exec(k);
    if (m && isNum(v)) { cols.add(m[2]); by.set(m[1], { ...(by.get(m[1]) ?? {}), [m[2]]: v }); } else rest.push([k, v]);
  }
  if (by.size < 2) return null;
  return { rows: CLASSES.filter((c) => by.has(c)).map((c) => ({ cls: c, vals: by.get(c)! })), cols: [...cols], rest };
}
const COL_LABEL: Record<string, string> = { Se: 'Sensitivity', '+P': 'Positive predictivity', F1: 'F1', PPV: 'Positive predictivity', Sp: 'Specificity' };

/** Renders whatever the API returned — nothing here is hard-coded. */
function Tree({ value, depth = 0 }: { value: unknown; depth?: number }): ReactNode {
  if (isMatrix(value)) {
    const square4 = value.length === 4 && value.every((r) => r.length === 4);
    return (
      <div className="overflow-x-auto">
        <table className="w-auto">
          {square4 && <thead><tr><th scope="col">true ↓ / predicted →</th>{CLASSES.map((c) => <th key={c} scope="col" className="text-right">{c}</th>)}</tr></thead>}
          <tbody>
            {value.map((row, i) => (
              <tr key={i}>
                {square4 && <th scope="row" className="border-b border-line/60 text-fg">{CLASSES[i]}</th>}
                {row.map((c, j) => <td key={j} className={`num text-right ${square4 && i === j ? 'font-semibold text-fg' : 'text-fg-muted'}`}>{fmtVal(c)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  if (isRows(value)) {
    const cols = [...new Set(value.flatMap((r) => Object.keys(r)))].filter((c) => value.every((r) => !isObj(r[c]) && !Array.isArray(r[c])));
    return (
      <div className="overflow-x-auto">
        <table>
          <thead><tr>{cols.map((c) => <th key={c} scope="col" className={isNum(value[0][c]) ? 'text-right' : ''}>{humanKey(c)}</th>)}</tr></thead>
          <tbody>{value.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c} className={isNum(r[c]) ? 'num text-right' : ''}>{fmtVal(r[c])}</td>)}</tr>)}</tbody>
        </table>
      </div>
    );
  }
  if (isObj(value)) {
    const pc = perClass(value);
    const entries = pc ? pc.rest : Object.entries(value);
    const scalars = entries.filter(([, v]) => !isObj(v) && !isMatrix(v) && !isRows(v));
    const nested = entries.filter(([, v]) => isObj(v) || isMatrix(v) || isRows(v));
    return (
      <div className="space-y-3">
        {scalars.length > 0 && (
          <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-sm md:grid-cols-[minmax(10rem,auto)_1fr]">
            {scalars.map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-fg-muted">{humanKey(k)}</dt>
                <dd className={isNum(v) ? 'num text-fg' : 'text-fg'}>{fmtVal(v)}</dd>
              </div>
            ))}
          </dl>
        )}
        {pc && (
          <div className="overflow-x-auto">
            <table>
              <thead><tr><th scope="col">Class</th>{pc.cols.map((c) => <th key={c} scope="col" className="text-right">{COL_LABEL[c] ?? c}</th>)}</tr></thead>
              <tbody>
                {pc.rows.map((r) => (
                  <tr key={r.cls}><th scope="row" className="border-b border-line/60 font-mono text-fg">{r.cls}</th>{pc.cols.map((c) => <td key={c} className="num text-right">{fmtVal(r.vals[c])}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {nested.map(([k, v]) => (
          <div key={k} className={depth > 0 ? 'border-l border-line pl-3' : ''}>
            <h3 className="mb-1 text-xs font-semibold text-fg-muted">{humanKey(k)}</h3>
            <Tree value={v} depth={depth + 1} />
          </div>
        ))}
      </div>
    );
  }
  return <span className="text-sm text-fg">{fmtVal(value)}</span>;
}

const SHOWN = new Set(['model_version', 'stand_in', 'stand_in_note', 'evaluation', 'metrics', 'evaluation_note', 'limitations', 'architecture', 'classes', 'unknown_threshold']);

export default function AboutPage() {
  const q = useQuery({ queryKey: qk.meta, queryFn: api.meta });
  if (q.isPending) return <LoadingState label="Loading model information…" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} title="Could not load model information" />;
  const meta: Meta = q.data;
  const model: ModelInfo = { ...(isObj(meta.model) ? meta.model : {}) };
  const standIn = model.stand_in ?? meta.stand_in ?? (isObj(model.evaluation) ? (model.evaluation.stand_in as boolean | undefined) : undefined);
  const standInNote = model.stand_in_note ?? meta.stand_in_note;
  const evaluation = model.evaluation ?? model.metrics ?? meta.evaluation ?? meta.metrics ?? null;
  const limitations = model.limitations ?? meta.limitations ?? [];
  const other = Object.fromEntries(Object.entries(model).filter(([k]) => !SHOWN.has(k)));
  const hasEval = isObj(evaluation) && Object.keys(evaluation).length > 0;

  return (
    <div>
      <PageHeader title="About the model" sub="What labels the beats, how it was evaluated, and where it is known to be weak. Everything on this page comes from the running service." />
      <div className="space-y-3">
        {!meta.model_loaded && (
          <Banner tone="warn" title="Rules-only mode — no beat classifier is loaded.">Beats are labelled Unknown. Rate and pause rules still run.</Banner>
        )}
        {standIn && (
          <Banner tone="warn" title="Stand-in model.">
            {standInNote ?? 'The loaded weights are a stand-in, not the release model. The figures below describe the stand-in only.'}
          </Banner>
        )}

        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          <Card title="Versions">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
              <dt className="text-fg-muted">Model version</dt><dd><Mono className="text-fg">{meta.model_version ?? model.model_version ?? '—'}</Mono></dd>
              <dt className="text-fg-muted">Model loaded</dt><dd className="text-fg">{meta.model_loaded ? 'Yes' : 'No — rules only'}</dd>
              <dt className="text-fg-muted">Rule version</dt><dd><Mono className="text-fg">{meta.rule_version}</Mono></dd>
              {model.architecture && (<><dt className="text-fg-muted">Architecture</dt><dd className="text-fg">{model.architecture}</dd></>)}
              {isNum(model.unknown_threshold) && (<><dt className="text-fg-muted">Unknown threshold</dt><dd className="text-fg">Beats with a top probability below <span className="num">{model.unknown_threshold}</span> are shown as Unknown</dd></>)}
            </dl>
            <div className="mt-3 border-t border-line pt-3">
              <div className="label mb-1.5">Beat classes (AAMI)</div>
              <BeatLegend />
            </div>
          </Card>

          <Card title="Known limitations">
            {limitations.length > 0 ? (
              <ul className="list-disc space-y-1.5 pl-4 text-sm text-fg">{limitations.map((l, i) => <li key={i}>{l}</li>)}</ul>
            ) : (
              <EmptyState title="The service did not report limitations">Treat every label as unverified.</EmptyState>
            )}
            <ul className="mt-3 list-disc space-y-1 border-t border-line pt-3 pl-4 text-xs text-fg-muted">
              <li>F and Unknown beats never raise an alert on their own; S-only findings never go above Amber.</li>
              <li>An irregular rhythm is flagged as “Irregular rhythm — review”; the system does not name a rhythm disorder.</li>
              <li>Low signal quality or lead-off suppresses clinical alerts.</li>
            </ul>
          </Card>
        </div>

        <Card title="Evaluation" right={<span className="text-2xs text-fg-faint">as reported by GET /v1/meta</span>}>
          {model.evaluation_note && <p className="mb-3 text-sm text-fg-muted">{model.evaluation_note}</p>}
          {hasEval ? <Tree value={evaluation} /> : (
            <EmptyState title="No evaluation metrics were reported by the service">
              This page shows only what the API returns. No figures are quoted from documents.
            </EmptyState>
          )}
        </Card>

        {Object.keys(other).length > 0 && (
          <details className="panel">
            <summary className="cursor-pointer px-3 py-2 text-sm font-semibold">Model configuration (all reported fields)</summary>
            <div className="border-t border-line p-3"><Tree value={other} /></div>
          </details>
        )}
        <p className="text-xs text-fg-muted">{meta.disclaimer}</p>
      </div>
    </div>
  );
}
