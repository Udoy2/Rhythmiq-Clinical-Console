import { useCallback, useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useStore } from 'zustand';
import { api, qk } from '../api/endpoints';
import { BeatRibbon } from '../components/ecg/BeatRibbon';
import { EcgCanvas } from '../components/ecg/EcgCanvas';
import { BackIcon, EcgToolbar, ForwardIcon, IconTipButton, type EcgViewSettings } from '../components/ecg/EcgToolbar';
import { RiskGauge } from '../components/ecg/RiskGauge';
import { AlertsPanel } from '../components/live/AlertsPanel';
import { DemoControls } from '../components/live/DemoControls';
import { InsightPanel } from '../components/live/InsightPanel';
import { LiveBanners } from '../components/live/LiveBanners';
import { RhythmSection, VitalsDetail } from '../components/live/RhythmCard';
import { StateStrip } from '../components/live/StateStrip';
import { Badge, BeatLegend, Button, Card, SideSection } from '../components/ui';
import { agreementStats } from '../live/beatMatch';
import type { LiveFeed } from '../live/liveFeed';
import { alertsSuppressed } from '../live/liveReducer';
import { computeScale } from '../live/scale';
import { clearViewPause, readViewPause, writeViewPause } from '../live/viewPause';
import { canActOnAlerts, canRunDemo, canUseCopilot, useAuth } from '../state/auth';
import { useToasts } from '../state/toasts';
import { usePatient } from './PatientLayout';

/** "DEMO / REPLAY — record 208 (MIT-BIH)" */
export function replayBadgeText(s: { record?: string | null; dataset?: string | null }): string {
  const rec = s.record ? ` — record ${s.record}${s.dataset ? ` (${s.dataset})` : ''}` : '';
  return `DEMO / REPLAY${rec}`;
}

export function LiveConsolePage() {
  const { patient, feed } = usePatient();
  const role = useAuth((s) => s.user?.role);
  const s = useStore(feed.store);
  const pushToast = useToasts((t) => t.push);

  // ── view state: live, or paused at an absolute stream time ──
  // Pause/head are remembered per patient; feed lives on PatientLayout so the ring survives tab switches.
  const [settings, setSettings] = useState<EcgViewSettings>({ speedMmS: 25, gainMmMv: 10, showTruth: true });
  const saved = readViewPause(patient.id);
  const view = useRef({ paused: !!saved?.paused, head: saved && saved.paused ? saved.head : 0 });
  const [paused, setPaused] = useState(() => !!saved?.paused);
  const viewRange = useRef<{ from: number; to: number } | null>(null);
  const windowS = useRef(6);
  windowS.current = computeScale(900, settings.speedMmS, settings.gainMmMv).windowS;
  const knownSession = useRef<string | null | undefined>(undefined);

  const persistView = useCallback(() => {
    if (view.current.paused) writeViewPause(patient.id, { paused: true, head: view.current.head });
    else writeViewPause(patient.id, { paused: false });
  }, [patient.id]);

  const getHead = useCallback((now: number) => (view.current.paused ? view.current.head : feed.clock.now(now)), [feed]);
  const clampHead = useCallback((t: number) => {
    if (feed.ring.isEmpty) return t; // do not start PlayoutClock before samples arrive
    const live = feed.clock.now(performance.now());
    const min = Math.min(live, feed.ring.startTime + Math.min(windowS.current, 2));
    return Math.max(min, Math.min(live, t));
  }, [feed]);
  const pause = useCallback(() => {
    if (view.current.paused) return;
    if (!feed.store.getState().session?.active || feed.ring.isEmpty) return;
    view.current = { paused: true, head: feed.clock.now(performance.now()) };
    setPaused(true);
    writeViewPause(patient.id, { paused: true, head: view.current.head });
  }, [feed, patient.id]);
  const resume = useCallback(() => {
    view.current.paused = false;
    setPaused(false);
    writeViewPause(patient.id, { paused: false });
  }, [patient.id]);
  const onPan = useCallback((deltaS: number) => {
    // Match toolbar ▶: no forward pan (and no pause) while live.
    if (deltaS < 0 && !view.current.paused) return;
    pause();
    if (!view.current.paused) return;
    view.current.head = clampHead(view.current.head - deltaS);
    persistView();
  }, [pause, clampHead, persistView]);
  const onSeek = useCallback((t: number) => {
    pause();
    if (!view.current.paused) return;
    view.current.head = clampHead(t + windowS.current / 2);
    persistView();
  }, [pause, clampHead, persistView]);
  const togglePause = useCallback(() => (view.current.paused ? resume() : pause()), [pause, resume]);

  // Re-clamp a restored pause head if the ring pruned old samples (still the same feed across tabs).
  useEffect(() => {
    if (!view.current.paused || feed.ring.isEmpty) return;
    const next = clampHead(view.current.head);
    if (next !== view.current.head) {
      view.current.head = next;
      persistView();
    }
  }, [s.session?.session_id, s.conn, clampHead, persistView, feed]);

  // Drop pause when the session ends — do not show Resume with "No active stream".
  useEffect(() => {
    if (s.session && s.session.active === false && paused) resume();
  }, [s.session, paused, resume]);

  // In-place stream restart (buffers cleared, same session_id): leave the stuck paused head.
  const bufferEpoch = s.bufferEpoch;
  const seenEpoch = useRef(bufferEpoch);
  useEffect(() => {
    if (bufferEpoch === seenEpoch.current) return;
    seenEpoch.current = bufferEpoch;
    if (!view.current.paused) return;
    clearViewPause(patient.id);
    resume();
  }, [bufferEpoch, resume, patient.id]);

  // Auto-resume only when one active session is replaced by another (e.g. Restart replay).
  // Do NOT treat remount connect (null → sessionId) as a new session — that cleared pause before.
  const sessionId = s.session?.session_id;
  useEffect(() => {
    const next = sessionId ?? null;
    if (knownSession.current === undefined) {
      knownSession.current = next;
      return;
    }
    const prev = knownSession.current;
    if (next === prev) return;
    knownSession.current = next;
    if (prev && next && prev !== next) {
      clearViewPause(patient.id);
      resume();
    }
  }, [sessionId, resume, patient.id]);

  const backlog = useQuery({ queryKey: qk.alerts({ patient_id: patient.id, evidence: false }), queryFn: () => api.alerts({ patient_id: patient.id, evidence: false }) });
  useEffect(() => { backlog.data?.forEach((a) => feed.upsertAlert(a)); }, [backlog.data, feed]);
  useEffect(() => {
    for (const id of s.toastQueue) {
      const a = s.alerts.find((x) => x.id === id);
      if (a) pushToast({ id: `alert-${a.id}`, tone: 'alert', tier: a.tier, title: a.title, body: a.detail, href: `/alerts/${a.id}`, hrefLabel: 'Open evidence' });
      feed.dispatch({ kind: 'toast-shown', id });
    }
  }, [s.toastQueue, s.alerts, feed, pushToast]);

  const [detailsOpen, setDetailsOpen] = useState(false);
  const session = s.session;
  const isReplay = session?.source === 'replay';
  const suppressed = alertsSuppressed(s.quality.state);
  const risk = s.window?.risk ?? null;
  const patientAlerts = s.alerts.filter((a) => a.patient_id === patient.id);
  const openAlerts = patientAlerts.filter((a) => a.state !== 'closed').length;

  return (
    <div>
      <LiveBanners s={s} />
      <StateStrip risk={risk} hr={s.window?.hr_bpm} openAlerts={openAlerts} stale={s.conn !== 'open' || suppressed} />
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="min-w-0 space-y-3">
          <Card
            title={
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <h2>ECG</h2>
                {session && <span className="num text-xs text-fg-muted">{session.fs} Hz · {session.device_id}</span>}
                {isReplay && session && <Badge tone="amber" className="!text-xs max-sm:!whitespace-normal max-sm:!rounded-md" title="Replayed public recording — not a real patient stream">{replayBadgeText(session)}</Badge>}
                {session && !isReplay && session.active && <Badge tone="ok">LIVE DEVICE</Badge>}
              </div>
            }
            right={<ConnBadge conn={s.conn} active={!!session?.active} />}
          >
            <EcgToolbar
              settings={settings}
              onChange={setSettings}
              truthAvailable={!!isReplay}
              left={
                <div className="flex items-center gap-1.5">
                  <Button size="sm" onClick={togglePause} aria-pressed={paused} className="w-[4.5rem]">{paused ? 'Resume' : 'Pause'}</Button>
                  <IconTipButton label="Scroll back" onClick={() => onPan(windowS.current / 2)}><BackIcon /></IconTipButton>
                  <IconTipButton label="Scroll forward" onClick={() => onPan(-windowS.current / 2)} disabled={!paused}><ForwardIcon /></IconTipButton>
                  <Button size="sm" variant={paused ? 'primary' : 'secondary'} onClick={resume} disabled={!paused}>Go live</Button>
                </div>
              }
              right={<BehindLive feed={feed} view={view} paused={paused} />}
            />
            <div className="mt-2">
              <EcgCanvas
                source={feed}
                getHead={getHead}
                speedMmS={settings.speedMmS}
                gainMmMv={settings.gainMmMv}
                showTruth={settings.showTruth && !!isReplay}
                onPan={onPan}
                onTogglePause={togglePause}
                viewRef={viewRange}
                ariaLabel={`ECG of ${patient.name}`}
              />
            </div>
            <div className="mt-3">
              <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
                <span className="label">Beat ribbon — last 60 s, one block per beat</span>
                {isReplay && settings.showTruth && <Agreement feed={feed} />}
              </div>
              <BeatRibbon source={feed} getHead={getHead} viewRef={viewRange} onSeek={onSeek} />
              <BeatLegend className="mt-2" />
            </div>
            {!session?.active && s.conn === 'open' && (
              <p className="mt-3 rounded border border-dashed border-line px-3 py-2 text-sm text-fg-muted">
                No active stream for this patient. {canRunDemo(role)
                  ? <><button type="button" className="font-semibold text-accent-text underline" onClick={() => setDetailsOpen(true)}>Open demo controls</button> to start a replay, or connect the paired device.</>
                  : 'The waveform appears when the paired device starts streaming.'}
              </p>
            )}
          </Card>

        </div>

        <aside aria-label="Rhythm, risk and alerts" className="panel min-w-0 divide-y divide-line self-start !shadow-none">
          <RhythmSection window={s.window} suppressed={suppressed} />
          <SideSection title="Risk" right={<span className="text-xs text-fg-faint">from the rules engine, every 10 s</span>}>
            <RiskGauge risk={risk} />
          </SideSection>
          <AlertsPanel alerts={patientAlerts} canAct={canActOnAlerts(role)} onUpdated={(a) => feed.upsertAlert(a)} loading={backlog.isPending} error={backlog.error} />
          {canUseCopilot(role) && <InsightPanel patientId={patient.id} />}
          <details className="group px-4 py-3" open={detailsOpen} onToggle={(e) => setDetailsOpen(e.currentTarget.open)}>
            <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-bold tracking-tight [&::-webkit-details-marker]:hidden">
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="transition-transform group-open:rotate-90"><path d="m6 3 5 5-5 5" /></svg>
              More detail
              <span className="text-xs font-normal text-fg-muted">HRV, burdens, versions{canRunDemo(role) ? ', demo controls' : ''}</span>
            </summary>
            <div className="mt-3 space-y-4">
              <VitalsDetail window={s.window} session={session} />
              {canRunDemo(role) && (
                <div className="border-t border-line pt-3">
                  <DemoControls patientId={patient.id} isDemoPatient={patient.is_demo} session={session} stream={s.stream} />
                </div>
              )}
            </div>
          </details>
        </aside>
      </div>
    </div>
  );
}

function ConnBadge({ conn, active }: { conn: string; active: boolean }) {
  if (conn === 'open') return <Badge tone={active ? 'ok' : 'outline'}>{active ? 'CONNECTED' : 'CONNECTED — NO STREAM'}</Badge>;
  if (conn === 'reconnecting') return <Badge tone="amber">RECONNECTING</Badge>;
  if (conn === 'connecting') return <Badge tone="outline">CONNECTING</Badge>;
  return <Badge tone="outline">DISCONNECTED</Badge>;
}

/** "Paused — 12.4 s behind live", refreshed a few times a second while paused. */
function BehindLive({ feed, view, paused }: { feed: LiveFeed; view: { current: { paused: boolean; head: number } }; paused: boolean }) {
  const [behind, setBehind] = useState(0);
  useEffect(() => {
    if (!paused) return;
    const tick = () => setBehind(Math.max(0, feed.clock.now(performance.now()) - view.current.head));
    tick();
    const t = setInterval(tick, 250);
    return () => clearInterval(t);
  }, [paused, feed, view]);
  if (!paused) return <span className="text-xs text-fg-faint">Drag the trace or use ← to scroll back</span>;
  return <span className="num text-xs font-medium text-amber-text" role="status">Paused — {behind.toFixed(1)} s behind live</span>;
}

/** Model vs. cardiologist agreement over the beats kept in history (replay only). */
function Agreement({ feed }: { feed: LiveFeed }) {
  const [text, setText] = useState('');
  useEffect(() => {
    const tick = () => {
      const st = agreementStats(feed.beats.items);
      setText(st.withTruth ? `Model agrees with ground truth on ${st.pct?.toFixed(1)}% of ${st.withTruth} beats (${st.mismatch} differ)` : '');
    };
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, [feed]);
  return text ? <span className="num text-xs text-fg-muted">{text}</span> : null;
}
