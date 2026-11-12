/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    // ponytail: one proxy per backend path; move the API under /api when the list grows
    proxy: Object.fromEntries(
      ['/health', '/customers', '/claims'].map((p) => [
        p,
        { target: process.env.BACKEND_URL ?? 'http://localhost:8000' },
      ]),
    ),
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/setupTests.ts'],
  },
})
