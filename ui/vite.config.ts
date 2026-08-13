import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'


const here = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))


export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(here, './src') } },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',


    chunkSizeWarningLimit: 900,
  },
  server: {
    port: 5173,


    proxy: { '/api': { target: 'http://127.0.0.1:8778', changeOrigin: true } },
  },
})
