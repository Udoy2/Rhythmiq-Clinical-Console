# CardioSense AI — frontend

Vite + React 18 + TypeScript (strict) + Tailwind CSS + React Router + TanStack Query + zustand.
Charts and the ECG are hand-rolled (canvas / SVG); there is no UI kit and no chart library.

> Research and demonstration system — not a medical device. Not an emergency service.

## Run

```bash
cd frontend
npm install            # see "node_modules on the 9p mount" below
npm run dev            # http://localhost:5173 — proxies /v1 (REST + WebSocket) to http://localhost:8000
npm run dev:mock       # same UI, no backend: REST and the live WebSocket are simulated in the browser
npm run build          # tsc --noEmit + vite build -> dist/
npm run build:mock     # mock build -> dist-mock/ (for a backend-less demo: npx vite preview --outDir dist-mock)
npm run test           # vitest (pure logic)
npm run typecheck      # tsc --noEmit
```

- Backend elsewhere: `VITE_API_TARGET=http://host:port npm run dev` (also honoured by `npm run preview`).
- Demo accounts (backend seed, shown on the sign-in page in dev/mock): `clinician@`, `admin@`, `tech@`,
  `patient@cardiosense.dev`, password `CardioSense!23`. Mock mode adds `mfa@cardiosense.dev` (any 6 digits) to
  exercise the one-time-code step.
- Demo path with the real backend: sign in as clinician → open "Demo Patient — Critical" → **Demo replay → Start
  replay** (record 208) → watch labels, ribbon, risk and the alert toast → open the alert → acknowledge →
  AI insight → History → Reports → About the model.

### Mock mode (`VITE_MOCK=1`)

Everything under `src/mock/` and nothing else. It is a separate chunk loaded by a dynamic import in `main.tsx`
only when `VITE_MOCK=1`; the normal build does not contain it. It installs a fake `fetch` and a fake WebSocket
through `src/api/transport.ts`, so the application code is identical in both modes. A `MOCK DATA` badge is shown
in the header. The mock streams a synthetic PQRST signal with scripted ectopy, a toy labeller and toy rules — it
is for layout and interaction work, not a model. The demo-controls card gets a "Mock faults" strip (lead-off,
low SQI, gap, drop connection) to show the quality and reconnect banners.

### node_modules on the 9p mount

The repo sits on a Windows 9p mount. `npm install` directly in `frontend/` works (about a minute) and is what
is in place now. A symlink `frontend/node_modules -> ~/.cache/cardiosense/frontend_node_modules` was tried
first, but npm replaces a symlinked `node_modules` with a real directory on install, so it is a plain directory.
The dev server uses polling file watching (`vite.config.ts`) because inotify is unreliable on 9p. If installs
become too slow, copy `frontend/` (without `node_modules`) to the Linux filesystem and work there.

## Layout

```
src/api/        types.ts (contract shapes), client.ts (auth, refresh, errors), endpoints.ts, normalize.ts, transport.ts
src/live/       ringBuffer, scale (px/mm), playout clocks, liveReducer, liveSocket (reconnect), liveFeed, beatMatch + tests
src/components/ ecg/ (EcgCanvas, BeatRibbon, RiskGauge, EvidenceStrip, beatStyle), charts/TrendChart, live/*, ui/*
src/pages/      SignIn, Patients, PatientLayout, LiveConsole, History, SessionDetail, Insights, Reports,
                Alerts, AlertDetail, Admin, About
src/mock/       dev mock (off by default)
```

## How the live console works

- `LiveFeed` owns one socket per open console. `signal` and `beat` messages go straight into a `SampleRing`
  (180 s, absolute sample index, gaps stored as NaN and never interpolated) and a `BeatStore`; they never touch
  React state. `session`, `window`, `alert`, `quality`, `status` go through the pure `reduceLive` reducer.
  Unknown message types and extra fields are ignored.
- `EcgCanvas` draws in `requestAnimationFrame`, devicePixelRatio-aware, and skips frames when nothing changed.
  The right edge follows a `PlayoutClock`: 220 ms behind the newest sample, advancing at the measured stream
  rate, so drawing is smooth regardless of when frames arrive.
- Geometry: 6 s at 25 mm/s (150 mm) spans the panel width, which fixes px/mm; gain and speed change only the
  scale. The grid (1 mm / 5 mm) is attached to the paper and scrolls with the trace.
- Pause, drag / arrow keys / horizontal wheel to scroll back through the ring, click the ribbon to jump,
  `,` `.` step through beats, click a beat to pin its tooltip.
- Reconnect: exponential backoff 0.5 s → 10 s with jitter, a fresh access token per attempt; close code 4401
  forces a token refresh, 4403 stops retrying. The last state stays on screen with the last sample time.
- Stored-session replay uses the same canvas with a deterministic `ReplayClock` and loads the signal in 60 s
  chunks (`/sessions/{id}/signal`).

## Encoding (never colour alone)

| Beat | Letter | Marker | Ribbon block |
|---|---|---|---|
| Normal | N | teal circle | solid, half height |
| Supraventricular | S | amber triangle | diagonal hatch, 3/4 height |
| Ventricular | V | coral square | barred, full height |
| Fusion | F | violet diamond | dotted, 3/4 height |
| Unknown | ? | grey ring | dashed outline |

Tiers always show name + meaning + shape: Green — Normal (circle), Amber — Watch (triangle),
Orange — Urgent (diamond), Red — Critical (octagon).
