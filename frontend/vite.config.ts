/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const API = process.env.VITE_API_TARGET ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    host: true,
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
