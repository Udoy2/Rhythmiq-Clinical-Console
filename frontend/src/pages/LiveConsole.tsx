import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useStore } from 'zustand';
import { api, qk } from '../api/endpoints';
import { BeatRibbon } from '../components/ecg/BeatRibbon';
import { EcgCanvas } from '../components/ecg/EcgCanvas';
import { EcgToolbar, type EcgViewSettings } from '../components/ecg/EcgToolbar';
import { RiskGauge } from '../components/ecg/RiskGauge';
import { AlertsPanel } from '../components/live/AlertsPanel';
import { DemoControls } from '../components/live/DemoControls';
import { InsightPanel } from '../components/live/InsightPanel';
import { LiveBanners } from '../components/live/LiveBanners';
import { RhythmCard, VitalsStrip } from '../components/live/RhythmCard';
import { Badge, BeatLegend, Button, Card } from '../components/ui';
import { agreementStats } from '../live/beatMatch';
import { LiveFeed } from '../live/liveFeed';
import { alertsSuppressed } from '../live/liveReducer';
import { computeScale } from '../live/scale';
import { canActOnAlerts, canRunDemo, canUseCopilot, useAuth } from '../state/auth';
import { useToasts } from '../state/toasts';
import { usePatient } from './PatientLayout';

function useLiveFeed(patientId: string): LiveFeed {
  const feed = useMemo(() => new LiveFeed(patientId), [patientId]);
  useEffect(() => {
    feed.start();
    return () => feed.stop();
  }, [feed]);
  return feed;
}

/** "DEMO / REPLAY — record 208 (MIT-BIH)" */
export function replayBadgeText(s: { record?: string | null; dataset?: string | null }): string {
  const rec = s.record ? ` — record ${s.record}${s.dataset ? ` (${s.dataset})` : ''}` : '';
  return `DEMO / REPLAY${rec}`;
}

export function LiveConsolePage() {
  const { patient } = usePatient();
  const role = useAuth((s) => s.user?.role);
  const feed = useLiveFeed(patient.id);
  const s = useStore(feed.store);
  const pushToast = useToasts((t) => t.push);

  // ── view state: live, or paused at an absolute stream time ──
  const [settings, setSettings] = useState<EcgViewSettings>({ speedMmS: 25, gainMmMv: 10, showTruth: true });
  const view = useRef({ paused: false, head: 0 });
  const [paused, setPaused] = useState(false);
  const viewRange = useRef<{ from: number; to: number } | null>(null);
  const windowS = useRef(6);
  windowS.current = computeScale(900, settings.speedMmS, settings.gainMmMv).windowS;

  const getLiveHead = useCallback((now: number) => feed.clock.now(now), [feed]);
  const getHead = useCallback((now: number) => (view.current.paused ? view.current.head : feed.clock.now(now)), [feed]);
  const clampHead = useCallback((t: number) => {
    const live = feed.clock.now(performance.now());
    const min = Math.min(live, feed.ring.startTime + Math.min(windowS.current, 2));
    return Math.max(min, Math.min(live, t));
  }, [feed]);
  const pause = useCallback(() => {
    if (view.current.paused) return;
    view.current = { paused: true, head: feed.clock.now(performance.now()) };
    setPaused(true);
  }, [feed]);
  const resume = useCallback(() => { view.current.paused = false; setPaused(false); }, []);
  const onPan = useCallback((deltaS: number) => {
    pause();
    view.current.head = clampHead(view.current.head - deltaS);
  }, [pause, clampHead]);
  const onSeek = useCallback((t: number) => {
    pause();
    view.current.head = clampHead(t + windowS.current / 2);
  }, [pause, clampHead]);
  const togglePause = useCallback(() => (view.current.paused ? resume() : pause()), [pause, resume]);

  // a new session starts from live
  const sessionId = s.session?.session_id;
  useEffect(() => { resume(); }, [sessionId, resume]);

  // ── alerts: REST backlog merged with what arrives on the socket ──
  const backlog = useQuery({ queryKey: qk.alerts({ patient_id: patient.id, evidence: false }), queryFn: () => api.alerts({ patient_id: patient.id, evidence: false }) });
  useEffect(() => { backlog.data?.forEach((a) => feed.upsertAlert(a)); }, [backlog.data, feed]);
  useEffect(() => {
    for (const id of s.toastQueue) {
      const a = s.alerts.find((x) => x.id === id);
      if (a) pushToast({ id: `alert-${a.id}`, tone: 'alert', tier: a.tier, title: a.title, body: a.detail, href: `/alerts/${a.id}`, hrefLabel: 'Open evidence' });
      feed.dispatch({ kind: 'toast-shown', id });
    }
  }, [s.toastQueue, s.alerts, feed, pushToast]);

  const session = s.session;
  const isReplay = session?.source === 'replay';
  const suppressed = alertsSuppressed(s.quality.state);
  const risk = s.window?.risk ?? null;
  const patientAlerts = s.alerts.filter((a) => a.patient_id === patient.id);

  return (
    <div>
      <LiveBanners s={s} />
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="min-w-0 space-y-3">
          <Card
            title={
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <h2>ECG</h2>
                {session && <span className="num text-xs text-fg-muted">{session.fs} Hz · {session.device_id}</span>}
                {isReplay && session && <Badge tone="amber" className="!text-xs" title="Replayed public recording — not a real patient stream">{replayBadgeText(session)}</Badge>}
                {session && !isReplay && session.active && <Badge tone="teal">LIVE DEVICE</Badge>}
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
                  <Button size="sm" onClick={() => onPan(windowS.current / 2)} aria-label="Scroll back">◀</Button>
                  <Button size="sm" onClick={() => onPan(-windowS.current / 2)} disabled={!paused} aria-label="Scroll forward">▶</Button>
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
              <BeatRibbon source={feed} getHead={getLiveHead} viewRef={viewRange} onSeek={onSeek} />
              <BeatLegend className="mt-2" />
            </div>
            {!session?.active && s.conn === 'open' && (
              <p className="mt-3 rounded border border-dashed border-line px-3 py-2 text-sm text-fg-muted">
                No active stream for this patient. {canRunDemo(role) ? 'Start a demo replay below, or connect the paired device.' : 'The waveform appears when the paired device starts streaming.'}
              </p>
            )}
          </Card>

          <VitalsStrip window={s.window} />
          {canRunDemo(role) && <DemoControls patientId={patient.id} isDemoPatient={patient.is_demo} session={session} />}
        </div>

        <div className="grid min-w-0 grid-cols-1 content-start gap-3 md:grid-cols-2 xl:grid-cols-1">
          <RhythmCard window={s.window} session={session} suppressed={suppressed} />
          <Card title="Risk" right={<span className="text-2xs text-fg-faint">from the rules engine, every 10 s</span>}>
            <RiskGauge risk={risk} />
          </Card>
          <AlertsPanel alerts={patientAlerts} canAct={canActOnAlerts(role)} onUpdated={(a) => feed.upsertAlert(a)} loading={backlog.isPending} error={backlog.error} />
          {canUseCopilot(role) && <InsightPanel patientId={patient.id} />}
        </div>
      </div>
    </div>
  );
}

function ConnBadge({ conn, active }: { conn: string; active: boolean }) {
  if (conn === 'open') return <Badge tone={active ? 'teal' : 'outline'}>{active ? 'CONNECTED' : 'CONNECTED — NO STREAM'}</Badge>;
  if (conn === 'reconnecting') return <Badge tone="amber">RECONNECTING</Badge>;
  if (conn === 'connecting') return <Badge tone="outline">CONNECTING</Badge>;
  return <Badge tone="outline">DISCONNECTED</Badge>;
}

/** "Paused — 12.4 s behind live", refreshed a few times a second while paused. */
function BehindLive({ feed, view, paused }: { feed: LiveFeed; view: { current: { paused: boolean; head: number } }; paused: boolean }) {
  const [behind, setBehind] = useState(0);
  useEffect(() => {
    if (!paused) return;
    const tick = () => setBehind(Math.max(0, feed.clock.headS - view.current.head));
    tick();
    const t = setInterval(tick, 250);
    return () => clearInterval(t);
  }, [paused, feed, view]);
  if (!paused) return <span className="text-xs text-fg-faint">Drag the trace or use ← → to scroll back</span>;
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
