import { fileURLToPath, URL } from 'node:url'
import babel from '@rolldown/plugin-babel'
import { tanstackRouter } from '@tanstack/router-plugin/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

const frontendRoot = fileURLToPath(new URL('.', import.meta.url))
const uiPort = Number.parseInt(process.env.SOPHIE_DEBUG_UI_PORT ?? '5174', 10)
const apiOrigin = process.env.SOPHIE_DEBUG_API_ORIGIN ?? 'http://127.0.0.1:8079'

if (!Number.isSafeInteger(uiPort) || uiPort < 1 || uiPort > 65535) {
  throw new Error('SOPHIE_DEBUG_UI_PORT must be a valid TCP port')
}
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(apiOrigin)) {
  throw new Error('SOPHIE_DEBUG_API_ORIGIN must be a loopback HTTP origin')
}

export default defineConfig({
  plugins: [
    tanstackRouter({ target: 'react', autoCodeSplitting: true }),
    react(),
    babel({ plugins: ['babel-plugin-ttag'] }),
  ],
  server: {
    host: '127.0.0.1',
    port: uiPort,
    strictPort: true,
    allowedHosts: ['127.0.0.1'],
    fs: { strict: true, allow: [frontendRoot] },
    proxy: {
      '/api': {
        target: apiOrigin,
        changeOrigin: false,
      },
    },
  },
  preview: {
    host: '127.0.0.1',
    port: uiPort,
    strictPort: true,
    allowedHosts: ['127.0.0.1'],
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: true,
  },
})
