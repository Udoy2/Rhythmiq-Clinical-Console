import { useEffect, useState } from 'react';
import type { LiveState } from '../../live/liveReducer';
import { fmtAgo, fmtTime } from '../../lib/format';
import { Banner } from '../ui';

function useNow(intervalMs: number, active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs, active]);
  return now;
}

const QUALITY_TEXT = {
  lead_off: { title: 'Lead-off — electrode contact lost.', hint: 'Check the electrodes and cable.' },
  low_sqi: { title: 'Low signal quality.', hint: 'Motion or noise is degrading the signal.' },
  gap: { title: 'Gap in the stream — samples are missing.', hint: 'Missing data is shown as a gap and is never interpolated.' },
} as const;

export function LiveBanners({ s }: { s: Pick<LiveState, 'conn' | 'denied' | 'attempt' | 'retryAt' | 'session' | 'quality' | 'stream' | 'lastSampleTime'> }) {
  const reconnecting = s.conn === 'reconnecting';
  const now = useNow(1000, reconnecting || s.stream === 'disconnected');
  const out: JSX.Element[] = [];

  if (s.denied) {
    out.push(<Banner key="deny" tone="critical" title="Live view is not available.">{s.denied}</Banner>);
  }

  if (reconnecting) {
    const retryIn = s.retryAt ? Math.max(0, Math.ceil((s.retryAt - now) / 1000)) : null;
    out.push(
      <Banner key="rc" tone="warn" title="Reconnecting…" live>
        The live connection dropped. The last known state stays on screen.{' '}
        {s.lastSampleTime
          ? <>Last sample at <span className="num text-fg">{fmtTime(s.lastSampleTime)}</span> ({fmtAgo(s.lastSampleTime, now)}).</>
          : 'No samples received yet.'}{' '}
        {retryIn !== null ? `Attempt ${s.attempt}, next try in ${retryIn} s.` : `Attempt ${Math.max(1, s.attempt)}…`}
      </Banner>,
    );
  }

  if (s.quality.state !== 'ok') {
    const q = QUALITY_TEXT[s.quality.state] ?? { title: 'Signal quality problem.', hint: '' };
    out.push(
      <Banner key="q" tone="warn" title={q.title}>
        <strong className="font-semibold text-fg">Clinical alerts are suppressed</strong> until the signal recovers; a device task is raised instead.{' '}
        {s.quality.detail || q.hint}
        {s.quality.sqi !== null && <> SQI <span className="num text-fg">{s.quality.sqi.toFixed(2)}</span>.</>}
      </Banner>,
    );
  }

  if (s.session && !s.session.model_loaded) {
    out.push(
      <Banner key="ro" tone="warn" title="Rules-only mode — the beat classifier is not loaded.">
        Beats are labelled Unknown. Rate and pause rules still run; beat-class findings and burdens are unavailable.
      </Banner>,
    );
  }

  if (!reconnecting && s.session && !s.session.active) {
    out.push(
      <Banner key="end" tone="info" title="Session ended.">
        The stream has stopped; the last state is shown.{' '}
        {s.lastSampleTime && <>Last sample at <span className="num text-fg">{fmtTime(s.lastSampleTime)}</span>.</>}
      </Banner>,
    );
  } else if (!reconnecting && s.session && s.stream === 'disconnected') {
    out.push(
      <Banner key="dis" tone="warn" title="Device stream disconnected.">
        No samples are arriving from the device; the last state is shown.{' '}
        {s.lastSampleTime && <>Last sample at <span className="num text-fg">{fmtTime(s.lastSampleTime)}</span> ({fmtAgo(s.lastSampleTime, now)}).</>}
      </Banner>,
    );
  }

  if (out.length === 0) return null;
  return <div className="mb-3 space-y-2">{out}</div>;
}
