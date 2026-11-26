import react from '@vitejs/plugin-react'
import { defineConfig, type ProxyOptions } from 'vite'

const API_TARGET = process.env.TACVECTOR_API ?? 'http://127.0.0.1:8000'

const proxy: Record<string, ProxyOptions> = {
  '/api': {
    target: API_TARGET,
    changeOrigin: true,
    configure: (p) => {
      // The backend answers 401 with `WWW-Authenticate: Basic`, which makes browsers pop their
      // native credentials dialog. Strip it so the app's own password modal handles auth.
      p.on('proxyRes', (res) => {
        delete res.headers['www-authenticate']
      })
    },
  },
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: { proxy },   // `npm run dev`
  preview: { proxy },  // `npm run preview` (serves the production build)
})
