import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'

// import.meta.dirname, not __dirname: Vite's native config loader does not provide the
// CommonJS globals and warns that it will stop supporting them.
const here = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))

// The build is served by advisor_api, from disk, on the same port as the API. Nothing is
// fetched from a CDN at runtime: this dashboard runs beside a game that is mid-campaign,
// often with no network, and a dashboard that needs the internet to render is a dashboard
// that goes blank exactly when a run is worth watching.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(here, './src') } },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    // Fail the build rather than ship a bundle that quietly grew. The client is a local
    // tool; there is no excuse for it to be large.
    chunkSizeWarningLimit: 900,
  },
  server: {
    port: 5173,
    // Dev only. In production the API and the client are one origin, so there is no
    // proxy and no CORS.
    proxy: { '/api': { target: 'http://127.0.0.1:8778', changeOrigin: true } },
  },
})
