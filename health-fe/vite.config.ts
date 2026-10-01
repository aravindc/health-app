/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { execSync } from 'node:child_process'

// Commit shown in index.html's <meta name="app-version">. Docker builds pass
// it in as GIT_SHA (the build context has no .git); local builds ask git.
function gitSha(): string {
  try {
    return execSync('git rev-parse --short HEAD', { stdio: ['ignore', 'pipe', 'ignore'] })
      .toString()
      .trim()
  } catch {
    return 'unknown'
  }
}
process.env.VITE_GIT_SHA ||= gitSha()

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
