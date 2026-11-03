/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite';
import vue from '@vitejs/plugin-vue';
import { defineConfig } from 'vite';

// The API the dev server proxies to. GARDEN_E2E_API_URL moves it for the browser tests (see e2e/urls.ts).
const API = process.env.GARDEN_E2E_API_URL ?? 'http://127.0.0.1:55000';

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  test: {
    include: ['src/**/*.test.ts'],
  },
  server: {
    proxy: {
      '/api': API,
      '/auth': API,
    },
  },
});
