/**
 * DEV MOCK ONLY. Loaded (dynamically) only when VITE_MOCK=1 — see main.tsx. Replaces the REST transport and
 * the live WebSocket with in-browser fakes so every screen works without the backend.
 */
import { transport } from '../api/transport';
import { patients } from './db';
import { mockFetch } from './rest';
import { MockSocket, mockControls, startStream, type MockControls } from './stream';

declare global {
  interface Window { __cardiosenseMock?: MockControls }
}

export function installMock(): void {
  transport.install(mockFetch, (url) => new MockSocket(url));
  window.__cardiosenseMock = mockControls;
  // Two demo patients stream from the start so the live console has something to show immediately;
  // the third ("watch", record 223) is offline until a replay is started from the demo controls.
  if (patients.some((p) => p.id === 'pt-demo-208')) startStream('pt-demo-208', '208');
  if (patients.some((p) => p.id === 'pt-demo-100')) startStream('pt-demo-100', '100');
  // eslint-disable-next-line no-console
  console.info('[CardioSense] MOCK MODE: REST and live WebSocket are simulated in the browser (src/mock).');
}
