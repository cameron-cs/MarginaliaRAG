import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// In development the UI runs on Vite (port 5180) and forwards API calls to the backend;
// in production the API serves the built files itself, from the same origin.
declare const process: { env: Record<string, string | undefined> }
const api = process.env.MARGINALIA_API ?? 'http://127.0.0.1:8010'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5180,
    proxy: { '/api': { target: api, ws: true, changeOrigin: true }, '/health': api },
  },
  test: { environment: 'jsdom', include: ['src/**/*.test.{ts,tsx}'] },
})
