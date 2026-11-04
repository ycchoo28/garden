import { defineConfig, devices } from '@playwright/test';

import { API_URL, WEB_URL } from './e2e/urls';

// The API and the background worker both embed text. The browser tests use a fake embeddings server
// (backend/tests/fake_embedding_server.py), so no test needs the bge-m3 model or an API key.
const EMBEDDING_URL = 'http://127.0.0.1:55084';

// The browser tests run against the dev stack: Vite proxies /api and /auth to the API.
// GARDEN_E2E_WEB_URL and GARDEN_E2E_API_URL move both (see e2e/urls.ts). The API reads the repo-root
// .env, which points at the fake sign-in provider that `just up` starts. `just e2e` points the API and
// the worker at a fresh database of their own through GARDEN_DATABASE_URL.
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
      command: `uv run python -m app.migrate && uv run python -m app.seed && uv run uvicorn app.main:app --host 127.0.0.1 --port ${new URL(API_URL).port}`,
      cwd: '../backend',
      url: `${API_URL}/api/health`,
      env: { GARDEN_EMBEDDING_URL: EMBEDDING_URL },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: `uv run python -m tests.fake_embedding_server --port ${new URL(EMBEDDING_URL).port}`,
      cwd: '../backend',
      url: `${EMBEDDING_URL}/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      // The worker migrates nothing, so it starts only once the API has migrated and seeded the database.
      command: `until curl --silent --fail ${API_URL}/api/health >/dev/null; do sleep 0.5; done && uv run python -m app.worker`,
      cwd: '../backend',
      wait: { stderr: /Starting worker/ },
      env: { GARDEN_EMBEDDING_URL: EMBEDDING_URL },
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
