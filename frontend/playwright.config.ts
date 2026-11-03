import { defineConfig, devices } from '@playwright/test';

import { API_URL, WEB_URL } from './e2e/urls';

// The browser tests run against the dev stack: Vite proxies /api and /auth to the API.
// GARDEN_E2E_WEB_URL and GARDEN_E2E_API_URL move both (see e2e/urls.ts). The API reads the repo-root
// .env, which points at the fake sign-in provider that `just up` starts.
export default defineConfig({
  testDir: 'e2e',
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [
    ['list'],
    ['html', { open: 'never' }],
    ['json', { outputFile: 'playwright-report/results.json' }],
  ],
  use: {
    baseURL: WEB_URL,
    screenshot: 'on',
    video: 'on',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: `uv run alembic upgrade heads && uv run python -m app.seed && uv run uvicorn app.main:app --host 127.0.0.1 --port ${new URL(API_URL).port}`,
      cwd: '../backend',
      url: `${API_URL}/api/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: `bun run dev --strictPort --port ${new URL(WEB_URL).port}`,
      url: WEB_URL,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
