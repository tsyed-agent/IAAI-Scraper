import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Built assets land inside the Python package so `cli serve` needs no Node.
export default defineConfig({
  plugins: [react()],
  base: '/ui/',
  build: {
    outDir: '../iaai_scraper/static/ui',
    emptyOutDir: true,
    assetsDir: 'assets',
  },
  server: {
    proxy: {
      '/lots': 'http://127.0.0.1:8000',
      '/filters': 'http://127.0.0.1:8000',
      '/stats': 'http://127.0.0.1:8000',
      '/commands': 'http://127.0.0.1:8000',
      '/branches': 'http://127.0.0.1:8000',
      '/healthz': 'http://127.0.0.1:8000',
    },
  },
})
