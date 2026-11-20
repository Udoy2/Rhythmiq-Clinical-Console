import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Build only: the dev server needs inline scripts (HMR). Mirrors security-headers.conf minus frame-ancestors (ignored in a meta tag).
const CSP = "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; frame-src 'self' blob:; object-src 'self' blob:; base-uri 'self'; form-action 'self'; manifest-src 'self'";
const cspMeta = {
  name: 'csp-meta',
  apply: 'build' as const,
  transformIndexHtml: () => [{ tag: 'meta', attrs: { 'http-equiv': 'Content-Security-Policy', content: CSP }, injectTo: 'head-prepend' as const }],
};

const API = process.env.VITE_API_TARGET ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react(), cspMeta],
  server: {
    port: 5173,
    strictPort: true,
    host: 'localhost',
    // The repo lives on a Windows 9p mount where inotify is unreliable.
    watch: { usePolling: true, interval: 400 },
    proxy: {
      '/v1': { target: API, changeOrigin: true, ws: true },
    },
  },
  preview: {
    port: 5173,
    proxy: { '/v1': { target: API, changeOrigin: true, ws: true } },
  },
  build: { target: 'es2020', sourcemap: false },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
});
