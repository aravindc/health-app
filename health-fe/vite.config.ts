/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Mirror nginx.conf's /api proxy for `npm run dev`, against health-api's
  // host port from docker-compose.yml.
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:9082',
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
  },
})
