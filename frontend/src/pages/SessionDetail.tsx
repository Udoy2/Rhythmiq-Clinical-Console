import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { errorText, saveBlob } from '../api/client';
import { api, qk } from '../api/endpoints';
import { flattenEvents } from '../api/normalize';
import type { Session } from '../api/types';
import { BeatRibbon } from '../components/ecg/BeatRibbon';
import { EcgCanvas } from '../components/ecg/EcgCanvas';
import { EcgToolbar, type EcgViewSettings } from '../components/ecg/EcgToolbar';
import { TIER_STYLE } from '../components/ecg/beatStyle';
import { Badge, Banner, BeatChip, BeatLegend, Button, Card, EmptyState, ErrorState, LoadingState, Mono, QueryState, Segmented, ShapeGlyph, Spinner } from '../components/ui';
import { BeatStore } from '../live/beatMatch';
import type { EcgSource } from '../live/liveFeed';
import { ReplayClock } from '../live/playout';
import { SampleRing } from '../live/ringBuffer';
import { computeScale, fmtClock } from '../live/scale';
import { fmtDateTime, fmtDuration, fmtNum, fmtPct } from '../lib/format';
import { toast } from '../state/toasts';
import { sessionDuration } from './History';
import { replayBadgeText } from './LiveConsole';

const CHUNK_S = 60;
const SPEEDS = [0.5, 1, 2, 4] as const;
/** Stored replay is capped so a very long session cannot exhaust memory; the rest stays reachable by CSV export. */
const MAX_REPLAY_S = 2 * 3600;

export default function SessionPage() {
  const { id = '', sessionId = '' } = useParams();
  const q = useQuery({ queryKey: qk.session(sessionId), queryFn: () => api.session(sessionId) });
  const csv = useMutation({
    mutationFn: () => api.sessionCsv(sessionId),
    onSuccess: ({ blob, filename }) => saveBlob(blob, filename ?? `session-${sessionId}.csv`),
    onError: (e) => toast.error('CSV export failed', errorText(e)),
  });

  if (q.isPending) return <LoadingState label="Loading session…" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} title="Could not load this session" />;
  const s = q.data;
  const dur = sessionDuration(s);
  const sm = s.summary ?? {};
  const counts = sm.beat_counts ?? null;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Link to={`/patients/${id}/history`} className="text-xs text-accent-text hover:underline">← History</Link>
        <h2 className="text-base">Session {fmtDateTime(s.started_at)}</h2>
        {s.source === 'replay' && <Badge tone="amber" className="!text-xs">{replayBadgeText(s)}</Badge>}
        {s.active && <Badge tone="ok">ACTIVE</Badge>}
        <div className="ml-auto"><Button size="sm" onClick={() => csv.mutate()} busy={csv.isPending}>Export CSV</Button></div>
      </div>

      <section aria-label="Session summary" className="panel grid grid-cols-2 gap-x-4 gap-y-2 p-3 text-sm md:grid-cols-4 xl:grid-cols-8">
        <Meta k="Duration" v={fmtDuration(dur)} />
        <Meta k="Sample rate" v={`${s.fs} Hz`} />
        <Meta k="Beats" v={fmtNum(sm.beats as number | null)} />
        <Meta k="Mean HR" v={`${fmtNum((sm.mean_hr_bpm ?? sm.hr_bpm) as number | null)} bpm`} />
        <Meta k="S burden" v={fmtPct(sm.s_burden_pct as number | null)} />
        <Meta k="V burden" v={fmtPct(sm.v_burden_pct as number | null)} />
        {typeof sm.truth_agreement_pct === 'number' && <Meta k="Agrees with ground truth" v={`${fmtPct(sm.truth_agreement_pct)} of ${fmtNum(sm.truth_matched as number | null)}`} />}
        <Meta k="Rules" v={<Mono>{s.rule_version ?? '—'}</Mono>} />
        <Meta k="Model" v={<Mono>{s.model_version ?? '—'}</Mono>} />
        {counts && (
          <div className="col-span-2 md:col-span-4 xl:col-span-8">
            <ul className="flex flex-wrap gap-x-4 gap-y-1 border-t border-line pt-2">
              {(['N', 'S', 'V', 'F', 'Unknown'] as const).map((l) => (
                <li key={l} className="inline-flex items-center gap-1.5"><BeatChip label={l} withName /><span className="num text-fg">{counts[l] ?? 0}</span></li>
              ))}
            </ul>
          </div>
        )}
      </section>

      {s.active ? (
        <Banner tone="info" title="This session is still recording." action={<Link to={`/patients/${id}/live`} className="text-sm font-medium text-accent-text underline">Open live console</Link>}>
          Replay becomes available when the stream ends.
        </Banner>
      ) : dur && dur > 0 ? (
        <ReplayPlayer key={s.id} session={s} durationS={Math.min(dur, MAX_REPLAY_S)} truncated={dur > MAX_REPLAY_S} />
      ) : (
        <Card title="Replay"><EmptyState title="No stored signal for this session" /></Card>
      )}
    </div>
  );
}

function Meta({ k, v }: { k: string; v: ReactNode }) {
  return <div className="min-w-0"><div className="label">{k}</div><div className="num truncate text-fg">{v}</div></div>;
}

function ReplayPlayer({ session, durationS, truncated }: { session: Session; durationS: number; truncated: boolean }) {
  const [params] = useSearchParams();
  const startAt = Math.min(durationS, Math.max(0, Number(params.get('t') ?? 0) || 0));

  // Buffers are created once per session; the whole timeline is pre-filled with NaN and chunks overwrite in place.
  const source = useMemo<EcgSource & { loaded: Set<number>; loading: Set<number> }>(() => {
    const ring = new SampleRing(session.fs, durationS + 1);
    ring.write(0, new Float32Array(ring.capacity).fill(NaN));
    return { ring, beats: new BeatStore(), loaded: new Set(), loading: new Set() };
  }, [session.fs, durationS]);
  const clock = useMemo(() => new ReplayClock(0, durationS), [durationS]);

  const [settings, setSettings] = useState<EcgViewSettings>({ speedMmS: 25, gainMmMv: 10, showTruth: true });
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<number>(1);
  const [pos, setPos] = useState(startAt);
  const [buffering, setBuffering] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const viewRange = useRef<{ from: number; to: number } | null>(null);
  const wantPlay = useRef(false);
  const windowS = computeScale(900, settings.speedMmS, settings.gainMmMv).windowS;

  // The right edge of the sweep leads the playhead position slightly at the very start so the first beats are visible.
  const getHead = useCallback((now: number) => clock.now(now), [clock]);

  const beatsQ = useQuery({
    queryKey: ['session', session.id, 'beats', durationS],
    queryFn: () => api.sessionBeats(session.id, 0, durationS, session.fs),
    staleTime: Infinity,
  });
  useEffect(() => {
    source.beats.clear();
    beatsQ.data?.forEach((b) => source.beats.add({ ...b, t: typeof b.sample === 'number' && session.fs ? b.sample / session.fs : b.time_s }));
  }, [beatsQ.data, source, session.fs]);
  const hasTruth = useMemo(() => !!beatsQ.data?.some((b) => b.truth !== null), [beatsQ.data]);

  const loadChunk = useCallback(async (c: number) => {
    if (c < 0 || c * CHUNK_S >= durationS || source.loaded.has(c) || source.loading.has(c)) return;
    source.loading.add(c);
    try {
      const chunk = await api.sessionSignal(session.id, c * CHUNK_S, Math.min(durationS, (c + 1) * CHUNK_S));
      const fs = chunk.fs || session.fs;
      // t0 is the absolute index of the first returned sample (at the chunk's own rate).
      const t0 = typeof chunk.t0 === 'number' ? chunk.t0 : Math.round(c * CHUNK_S * fs);
      if (fs !== source.ring.fs) {
        source.ring.reset(fs);
        source.ring.write(0, new Float32Array(source.ring.capacity).fill(NaN));
        source.loaded.clear();
      }
      source.ring.write(t0, chunk.mv ?? []);
      source.loaded.add(c);
      setLoadError(null);
    } catch (e) {
      setLoadError(errorText(e));
    } finally {
      source.loading.delete(c);
    }
  }, [session.id, session.fs, durationS, source]);

  // initial position
  useEffect(() => { clock.seek(Math.max(startAt, Math.min(durationS, windowS * 0.6)), performance.now()); }, [clock, startAt, durationS]); // eslint-disable-line react-hooks/exhaustive-deps

  // Position readout + chunk scheduling; playback waits while the chunk under the playhead is loading.
  useEffect(() => {
    const tick = () => {
      const now = performance.now();
      const t = clock.now(now);
      setPos(t);
      const c = Math.floor(Math.max(0, t - 0.001) / CHUNK_S);
      const cPrev = Math.floor(Math.max(0, t - windowS) / CHUNK_S);
      void loadChunk(c); void loadChunk(cPrev); void loadChunk(c + 1);
      const ready = source.loaded.has(c) && source.loaded.has(cPrev);
      setBuffering(!ready);
      if (!ready && clock.playing) clock.pause(now);
      else if (ready && wantPlay.current && !clock.playing && t < durationS) clock.play(now);
      if (wantPlay.current && t >= durationS) wantPlay.current = false;
      setPlaying(wantPlay.current);
    };
    tick();
    const iv = setInterval(tick, 200);
    return () => clearInterval(iv);
  }, [clock, loadChunk, source, durationS, windowS]);

  const play = () => { wantPlay.current = true; if (clock.now(performance.now()) >= durationS) clock.seek(0, performance.now()); clock.play(performance.now()); setPlaying(true); };
  const pause = () => { wantPlay.current = false; clock.pause(performance.now()); setPlaying(false); };
  const seek = useCallback((t: number) => { clock.seek(t, performance.now()); setPos(clock.now(performance.now())); }, [clock]);
  const onPan = useCallback((d: number) => { wantPlay.current = false; clock.pause(performance.now()); setPlaying(false); seek(clock.now(performance.now()) - d); }, [clock, seek]);
  const changeSpeed = (v: number) => { setSpeed(v); clock.setSpeed(v, performance.now()); };

  const events = useQuery({ queryKey: qk.sessionEvents(session.id), queryFn: () => api.sessionEvents(session.id, true) });

  return (
    <>
      <Card title="Replay" right={<span className="text-2xs text-fg-faint">Stored signal and stored labels — the same data every time</span>}>
        {truncated && <div className="mb-2"><Banner tone="info" title={`Replay covers the first ${fmtDuration(durationS)}.`}>Use CSV export for the full session.</Banner></div>}
        <EcgToolbar
          settings={settings}
          onChange={setSettings}
          truthAvailable={hasTruth}
          left={
            <div className="flex items-center gap-2">
              <Button size="sm" variant="primary" className="w-16" onClick={playing ? pause : play}>{playing ? 'Pause' : 'Play'}</Button>
              <div className="flex items-center gap-1.5">
                <span className="label">Playback</span>
                <Segmented label="Playback speed" value={speed} options={SPEEDS} onChange={changeSpeed} format={(v) => `${v}×`} />
              </div>
            </div>
          }
          right={buffering && !loadError ? <Spinner size={12} label="Loading signal…" /> : undefined}
        />
        <div className="mt-2 flex items-center gap-3">
          <span className="num w-14 text-right text-sm text-fg">{fmtClock(pos)}</span>
          <label htmlFor="scrub" className="sr-only">Position in session</label>
          <input id="scrub" type="range" min={0} max={durationS} step={0.5} value={pos} className="h-1.5 flex-1 accent-ink"
            aria-valuetext={`${fmtClock(pos)} of ${fmtClock(durationS)}`}
            onChange={(e) => { wantPlay.current = false; clock.pause(performance.now()); setPlaying(false); seek(Number(e.target.value)); }} />
          <span className="num w-14 text-sm text-fg-muted">{fmtClock(durationS)}</span>
        </div>
        {loadError && <div className="mt-2"><Banner tone="critical" title="Could not load the stored signal.">{loadError}</Banner></div>}
        {beatsQ.isError && <div className="mt-2"><Banner tone="critical" title="Could not load the stored beat labels.">{errorText(beatsQ.error)}</Banner></div>}
        <div className="mt-2">
          <EcgCanvas source={source} getHead={getHead} speedMmS={settings.speedMmS} gainMmMv={settings.gainMmMv}
            showTruth={settings.showTruth && hasTruth} onPan={onPan} onTogglePause={playing ? pause : play} viewRef={viewRange}
            ariaLabel="Stored session ECG" />
        </div>
        <div className="mt-3">
          <div className="label mb-1">Beat ribbon — 60 s up to the playhead</div>
          <BeatRibbon source={source} getHead={getHead} viewRef={viewRange} onSeek={(t) => onPan(clock.now(performance.now()) - (t + windowS / 2))} />
          <BeatLegend className="mt-2" />
        </div>
      </Card>

      <Card title="Events in this session" bodyClass="p-0">
        <QueryState query={events} isEmpty={(e) => flattenEvents(e).length === 0}
          empty={<EmptyState title="No rhythm findings or alerts in this session" />}>
          {(list) => (
            <ul className="max-h-80 divide-y divide-line/60 overflow-y-auto">
              {flattenEvents(list).map((e) => {
                const st = e.tier ? TIER_STYLE[e.tier] : null;
                return (
                  <li key={e.key} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-1.5 text-sm">
                    <button type="button" className="num w-14 shrink-0 text-left text-xs text-accent-text hover:underline" disabled={e.start_s === null}
                      onClick={() => e.start_s !== null && onPan(clock.now(performance.now()) - Math.min(durationS, e.start_s + windowS * 0.7))}
                      aria-label={`Go to ${fmtClock(e.start_s ?? 0)}`}>
                      {e.start_s !== null ? fmtClock(e.start_s) : '—'}
                    </button>
                    <span className="inline-flex w-20 shrink-0 items-center gap-1.5 text-xs" style={{ color: st?.text ?? '#6A6158' }}>
                      {st && <ShapeGlyph shape={st.shape} color={st.color} size={10} />}{st ? st.meaning : e.kind}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-fg" title={e.detail}>{e.label}{e.detail && e.detail !== e.label ? <span className="text-fg-muted"> — {e.detail}</span> : null}</span>
                    {e.kind === 'alert' && <Badge tone="alert">ALERT</Badge>}
                    {e.ruleId && <Mono>{e.ruleId}</Mono>}
                    {e.alertId && <Link to={`/alerts/${e.alertId}`} className="text-xs text-accent-text hover:underline">Open alert</Link>}
                  </li>
                );
              })}
            </ul>
          )}
        </QueryState>
      </Card>
    </>
  );
}
