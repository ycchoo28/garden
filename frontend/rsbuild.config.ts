import { defineConfig } from '@rsbuild/core';
import { pluginVue } from '@rsbuild/plugin-vue';
import tailwindcss from '@tailwindcss/postcss';

// The API the dev server proxies to. GARDEN_E2E_API_URL moves it for the browser tests (see e2e/urls.ts).
const API = process.env.GARDEN_E2E_API_URL ?? 'http://127.0.0.1:55000';

export default defineConfig({
  plugins: [pluginVue()],
  source: {
    entry: {
      index: './src/main.ts',
    },
  },
  html: {
    template: './index.html',
  },
  server: {
    strictPort: true,
    proxy: {
      '/api': API,
      '/auth': API,
    },
  },
  tools: {
    postcss: {
      postcssOptions: {
        plugins: [tailwindcss()],
      },
    },
  },
});
