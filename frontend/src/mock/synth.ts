/**
 * DEV MOCK ONLY. Synthetic single-lead ECG (sum-of-Gaussians PQRST) with scripted ectopy, a toy "model"
 * that labels beats, and toy rhythm rules. Deterministic for a given seed. None of this is clinical logic —
 * it exists so the screens can be built and shown without the backend.
 */
import type { Beat, BeatLabel, Finding, RhythmWindow, Tier } from '../api/types';

export function rng(seed: number): () => number {
  let a = seed >>> 0 || 1;
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
export function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}

export interface Profile { hr: number; v: number; s: number; f: number; couplet: number; run: number; bigeminy: number; note: string }
export const PROFILES: Record<string, Profile> = {
  '100': { hr: 74, v: 0.004, s: 0.012, f: 0, couplet: 0, run: 0, bigeminy: 0, note: 'Mostly normal rhythm' },
  '105': { hr: 84, v: 0.02, s: 0, f: 0, couplet: 0.05, run: 0, bigeminy: 0, note: 'Occasional V beats, noisy' },
  '119': { hr: 66, v: 0.1, s: 0, f: 0, couplet: 0.05, run: 0, bigeminy: 0.05, note: 'Frequent V beats, bigeminy' },
  '200': { hr: 88, v: 0.16, s: 0.01, f: 0.005, couplet: 0.2, run: 0.04, bigeminy: 0.02, note: 'Frequent V beats' },
  '208': { hr: 96, v: 0.2, s: 0, f: 0.05, couplet: 0.3, run: 0.1, bigeminy: 0.01, note: 'Frequent V beats, couplets, fusion beats' },
  '220': { hr: 70, v: 0, s: 0.05, f: 0, couplet: 0, run: 0, bigeminy: 0, note: 'S beats' },
  '223': { hr: 80, v: 0.07, s: 0.04, f: 0.005, couplet: 0.1, run: 0.02, bigeminy: 0, note: 'S and V beats' },
  '233': { hr: 100, v: 0.22, s: 0.003, f: 0.004, couplet: 0.15, run: 0.05, bigeminy: 0.02, note: 'Frequent V beats' },
};
export const profileFor = (record: string): Profile => PROFILES[record] ?? PROFILES['100'];

type Cls = 'N' | 'S' | 'V' | 'F';
interface SynthBeat { r: number; cls: Cls; pol: number; amp: number }

const g = (t: number, sd: number) => Math.exp(-0.5 * (t / sd) * (t / sd));
function wave(cls: Cls, t: number, pol: number): number {
  const n = () => 0.12 * g(t + 0.17, 0.022) - 0.1 * g(t + 0.028, 0.008) + 1.15 * g(t, 0.0095) - 0.24 * g(t - 0.028, 0.0095) + 0.3 * g(t - 0.27, 0.05);
  const v = () => pol * (1.55 * g(t, 0.034) - 0.95 * g(t - 0.09, 0.045) - 0.5 * g(t - 0.32, 0.075));
  switch (cls) {
    case 'N': return n();
    case 'S': return -0.05 * g(t + 0.12, 0.02) - 0.1 * g(t + 0.028, 0.008) + 1.0 * g(t, 0.0095) - 0.2 * g(t - 0.028, 0.0095) + 0.26 * g(t - 0.26, 0.05);
    case 'V': return v();
    case 'F': return 0.55 * n() + 0.45 * v();
  }
}

export interface SynthOut { mv: number[]; t0: number }

export class EcgSynth {
  readonly fs: number;
  private rand: () => number;
  private beats: SynthBeat[] = [];
  private nextR: number; // seconds
  private queue: Cls[] = [];
  private pos = 0; // samples generated
  private emitted = 0; // beats handed out via takeBeats
  private wander: number;
  private afterPremature = false;

  constructor(readonly profile: Profile, fs: number, seed: number) {
    this.fs = fs;
    this.rand = rng(seed);
    this.nextR = 0.45 + this.rand() * 0.3;
    this.wander = this.rand() * 6.28;
  }

  get position(): number { return this.pos; }

  private randn(): number {
    const u = Math.max(1e-9, this.rand()), v = this.rand();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(6.2832 * v);
  }

  private schedule(untilS: number): void {
    const p = this.profile;
    while (this.nextR < untilS) {
      const base = 60 / p.hr;
      let cls: Cls = this.queue.shift() ?? 'N';
      if (cls === 'N' && this.queue.length === 0 && !this.afterPremature) {
        const x = this.rand();
        if (x < p.v) {
          cls = 'V';
          const y = this.rand();
          if (y < p.run) this.queue.push(...Array.from({ length: 2 + Math.floor(this.rand() * 4) }, () => 'V' as Cls));
          else if (y < p.run + p.couplet) this.queue.push('V');
          else if (y < p.run + p.couplet + p.bigeminy * 4) for (let i = 0; i < 7; i++) this.queue.push('N', 'V');
        } else if (x < p.v + p.s) cls = 'S';
        else if (x < p.v + p.s + p.f) cls = 'F';
      }
      const prev = this.beats[this.beats.length - 1];
      let r = this.nextR;
      if (prev) {
        // prematurity for ectopic beats: pull this beat earlier
        if (cls === 'V') r = prev.r + (prev.cls === 'V' ? 0.4 + this.rand() * 0.06 : base * 0.62);
        else if (cls === 'S') r = prev.r + base * 0.68;
        else if (cls === 'F') r = prev.r + base * 0.85;
      }
      this.beats.push({ r, cls, pol: this.rand() < 0.8 ? 1 : -0.85, amp: 1 + 0.05 * this.randn() });
      const rr = base * (1 + 0.035 * this.randn() + 0.03 * Math.sin(r * 1.6));
      // compensatory pause after a ventricular beat when a normal beat follows
      const nextIsV = this.queue[0] === 'V';
      this.afterPremature = false;
      if (cls === 'V' && !nextIsV) { this.nextR = r + base * 1.36; this.afterPremature = true; }
      else this.nextR = r + Math.max(0.3, rr);
    }
  }

  /** Generate the next n samples. */
  next(n: number): SynthOut {
    const t0 = this.pos;
    const fs = this.fs;
    const endS = (t0 + n) / fs;
    this.schedule(endS + 1.2);
    const mv = new Array<number>(n);
    const lo = t0 / fs - 0.7, hi = endS + 0.4;
    const near = this.beats.filter((b) => b.r > lo && b.r < hi);
    for (let k = 0; k < n; k++) {
      const t = (t0 + k) / fs;
      let y = 0.035 * Math.sin(this.wander + t * 1.7) + 0.012 * this.randn();
      for (const b of near) {
        const dt = t - b.r;
        if (dt > -0.4 && dt < 0.6) y += b.amp * wave(b.cls, dt, b.pol);
      }
      mv[k] = Math.round(y * 1000) / 1000;
    }
    this.pos += n;
    return { mv, t0 };
  }

  /**
   * Beats whose label can be "finalised": like the real pipeline, a beat is emitted once the next beat's
   * R-peak has passed (post-RR is known).
   */
  takeBeats(): { sample: number; cls: Cls }[] {
    const out: { sample: number; cls: Cls }[] = [];
    const nowS = this.pos / this.fs;
    while (this.emitted + 1 < this.beats.length && this.beats[this.emitted + 1].r + 0.05 < nowS) {
      const b = this.beats[this.emitted++];
      out.push({ sample: Math.round(b.r * this.fs), cls: b.cls });
    }
    // keep memory bounded
    if (this.emitted > 400) { this.beats.splice(0, 300); this.emitted -= 300; }
    return out;
  }
}

/** Toy "model": mostly agrees with the truth, sometimes wrong or below the Unknown threshold. Deterministic. */
export function mockLabel(sample: number, truth: Cls, fs: number, modelVersion: string, seed: number): Beat {
  const r = rng(hash(`${seed}:${sample}`));
  const x = r();
  let label: BeatLabel = truth;
  let conf = 0.9 + 0.099 * r();
  const errRate = truth === 'N' ? 0.012 : truth === 'V' ? 0.06 : truth === 'S' ? 0.35 : 0.6;
  if (x < 0.012) { label = 'Unknown'; conf = 0.38 + 0.2 * r(); }
  else if (x < 0.012 + errRate) { label = truth === 'N' ? (r() < 0.5 ? 'S' : 'V') : 'N'; conf = 0.55 + 0.3 * r(); }
  const probs = { N: 0, S: 0, V: 0, F: 0 };
  const top: Cls = label === 'Unknown' ? truth : (label as Cls);
  const rest = 1 - conf;
  const others = (['N', 'S', 'V', 'F'] as Cls[]).filter((c) => c !== top);
  const w = others.map(() => 0.1 + r());
  const ws = w.reduce((a, b) => a + b, 0);
  probs[top] = conf;
  others.forEach((c, i) => { probs[c] = (rest * w[i]) / ws; });
  (Object.keys(probs) as Cls[]).forEach((c) => { probs[c] = Math.round(probs[c] * 1000) / 1000; });
  return { sample, time_s: Math.round((sample / fs) * 1000) / 1000, label, confidence: Math.round(conf * 1000) / 1000, probs, model_version: modelVersion, truth };
}

const median = (a: number[]) => { if (!a.length) return NaN; const s = [...a].sort((x, y) => x - y); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };

/** Toy rules over the beats of one 10 s window (+ the last 60 s for burden). */
export function mockWindow(id: string, startS: number, endS: number, beats: Beat[], lastMinute: Beat[], ruleVersion: string): RhythmWindow & { id: string } {
  const counts = { N: 0, S: 0, V: 0, F: 0, Unknown: 0 };
  beats.forEach((b) => { counts[b.label]++; });
  const rr: number[] = [];
  const nn: number[] = [];
  for (let i = 1; i < beats.length; i++) {
    const d = beats[i].time_s - beats[i - 1].time_s;
    if (beats[i].label === 'N' || beats[i].label === 'S') rr.push(d);
    if (beats[i].label === 'N' && beats[i - 1].label === 'N') nn.push(d * 1000);
  }
  const med = median(rr);
  const hr = Number.isFinite(med) ? Math.round((60 / med) * 10) / 10 : null;
  const mean = nn.reduce((a, b) => a + b, 0) / (nn.length || 1);
  const sdnn = nn.length > 2 ? Math.sqrt(nn.reduce((a, b) => a + (b - mean) ** 2, 0) / (nn.length - 1)) : null;
  let sq = 0;
  for (let i = 1; i < nn.length; i++) sq += (nn[i] - nn[i - 1]) ** 2;
  const rmssd = nn.length > 2 ? Math.sqrt(sq / (nn.length - 1)) : null;
  const known = lastMinute.filter((b) => b.label !== 'Unknown');
  const pct = (l: BeatLabel) => (known.length ? Math.round((1000 * known.filter((b) => b.label === l).length) / known.length) / 10 : 0);
  const vb = pct('V'), sb = pct('S');

  const findings: Finding[] = [];
  const add = (rule_id: string, label: string, severity: Tier, value: number | null, detail: string, s = startS, e = endS) =>
    findings.push({ rule_id, label, severity, start_s: Math.round(s * 100) / 100, end_s: Math.round(e * 100) / 100, value, detail });
  let run = 0, maxRun = 0, runStart = 0, bestStart = 0, bestEnd = 0;
  beats.forEach((b) => {
    if (b.label === 'V') { if (run === 0) runStart = b.time_s; run++; if (run > maxRun) { maxRun = run; bestStart = runStart; bestEnd = b.time_s; } }
    else if (b.label !== 'Unknown') run = 0;
  });
  if (maxRun >= 3) add('V_RUN', `V run (${maxRun} beats)`, 'orange', maxRun, `${maxRun} consecutive V beats`, bestStart, bestEnd);
  else if (maxRun === 2) add('V_COUPLET', 'V couplet', 'amber', 2, '2 consecutive V beats', bestStart, bestEnd);
  let alt = 0, maxAlt = 0;
  for (let i = 1; i < beats.length; i++) {
    if ((beats[i].label === 'V') !== (beats[i - 1].label === 'V')) { alt++; maxAlt = Math.max(maxAlt, alt); } else alt = 0;
  }
  if (maxAlt >= 8 && maxRun < 2) add('BIGEMINY', 'Bigeminy', 'amber', Math.floor(maxAlt / 2), 'V every 2nd beat');
  if (vb > 10) add('V_BURDEN', `V burden ${vb.toFixed(1)}%`, 'orange', vb, `V burden ${vb.toFixed(1)}% over the last minute`);
  else if (vb >= 1) add('V_BURDEN', `V burden ${vb.toFixed(1)}%`, 'amber', vb, `V burden ${vb.toFixed(1)}% over the last minute`);
  if (sb >= 1) add('S_BURDEN', `S burden ${sb.toFixed(1)}%`, 'amber', sb, `S burden ${sb.toFixed(1)}% over the last minute`);
  if (hr !== null && hr > 100) add('TACHY', `HR ${Math.round(hr)} bpm`, 'amber', hr, 'Heart rate above 100 bpm for 10 s');
  if (hr !== null && hr < 50) add('BRADY', `HR ${Math.round(hr)} bpm`, 'amber', hr, 'Heart rate below 50 bpm for 10 s');

  const factors = findings.map((f) => ({
    text: f.detail,
    rule_id: f.rule_id,
    points: f.rule_id === 'V_RUN' ? 30 : f.rule_id === 'V_BURDEN' ? Math.min(35, Math.round((f.value ?? 0) * 1.8)) : f.rule_id === 'V_COUPLET' ? 12
      : f.rule_id === 'BIGEMINY' ? 12 : f.rule_id === 'S_BURDEN' ? Math.min(15, Math.round((f.value ?? 0) * 2)) : 10,
  }));
  const score = Math.min(100, factors.reduce((a, f) => a + f.points, 0));
  const tier: Tier = score >= 75 ? 'red' : score >= 50 ? 'orange' : score >= 25 ? 'amber' : 'green';
  const unknownFrac = beats.length ? counts.Unknown / beats.length : 0;
  return {
    id, start_s: startS, end_s: endS, hr_bpm: hr, findings, s_burden_pct: sb, v_burden_pct: vb,
    sqi: Math.round((0.95 - unknownFrac * 0.8) * 100) / 100,
    sdnn_ms: sdnn === null ? null : Math.round(sdnn * 10) / 10, rmssd_ms: rmssd === null ? null : Math.round(rmssd * 10) / 10,
    beat_counts: counts, rule_version: ruleVersion, risk: { score, tier, factors },
  };
}

export function resample(mv: number[], fsIn: number, fsOut: number): number[] {
  if (fsIn === fsOut) return mv;
  const n = Math.floor((mv.length * fsOut) / fsIn);
  const out = new Array<number>(n);
  for (let i = 0; i < n; i++) {
    const x = (i * fsIn) / fsOut;
    const a = Math.floor(x), b = Math.min(mv.length - 1, a + 1);
    out[i] = Math.round((mv[a] + (mv[b] - mv[a]) * (x - a)) * 1000) / 1000;
  }
  return out;
}

/** Whole recording at once (stored-session replay, evidence strips). */
export function renderRecording(record: string, fs: number, seconds: number, seed: number, modelVersion: string) {
  const synth = new EcgSynth(profileFor(record), fs, seed);
  const mv: number[] = [];
  const beats: Beat[] = [];
  const chunk = Math.round(fs / 10);
  for (let done = 0; done < seconds * fs; done += chunk) {
    mv.push(...synth.next(chunk).mv);
    for (const b of synth.takeBeats()) beats.push(mockLabel(b.sample, b.cls, fs, modelVersion, seed));
  }
  return { mv, beats };
}
