import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 3000,
    open: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:5001',
        changeOrigin: true,
        secure: false,
        configure(proxy) {
          proxy.on('error', (_error, _request, response) => {
            if (!response || response.headersSent || response.writableEnded) return
            response.writeHead(503, { 'Content-Type': 'application/json', 'Retry-After': '1' })
            response.end(JSON.stringify({
              success: false,
              code: 'BACKEND_UNAVAILABLE',
              error: 'Backend unavailable; reconnecting'
            }))
          })
        }
      }
    }
  }
})
