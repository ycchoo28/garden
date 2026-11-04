import { expect, test } from '@playwright/test';

import { signIn } from './fake-sign-in';
import { WEB_URL } from './urls';

const MANAGER = 'priya.nair@fernhill.example';
const FINANCE = 'lena.fischer@fernhill.example';
const HEADER = 'transaction_ref,posted_on,department_code,category,merchant,amount_usd';

function today(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}

// A fresh database has no spend, so finance imports one card row per department this month first.
// Each run uses its own references, so a database that keeps rows from earlier runs still imports.
test.beforeAll(async ({ browser }) => {
  const context = await browser.newContext({ baseURL: WEB_URL });
  const page = await context.newPage();
  await signIn(page, FINANCE, 'Lena Fischer');
  for (const department of ['design', 'engineering']) {
    const ref = `variance-${department}-${Date.now()}`;
    const csv = `${HEADER}\n${ref},${today()},${department},software,Figma,12.34\n`;
    const response = await page.request.post('/api/spend/statements', {
      multipart: { file: { name: 'variance.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) } },
    });
    expect(response.status()).toBe(201);
  }
  await context.close();
});

test('a design manager sees only the design department on the variance page', async ({ page }) => {
  await signIn(page, MANAGER, 'Priya Nair');
  await page.goto('/budgets/variance');
  const table = page.getByRole('table', { name: /Budget variance from/ });
  await expect(table).toBeVisible();
  await expect(table.getByRole('row', { name: /Design/ }).first()).toBeVisible();
  await expect(table.getByRole('row', { name: /Engineering/ })).toHaveCount(0);
});

test('finance sees every department on the variance page', async ({ page }) => {
  await signIn(page, FINANCE, 'Lena Fischer');
  await page.goto('/budgets/variance');
  const table = page.getByRole('table', { name: /Budget variance from/ });
  await expect(table).toBeVisible();
  await expect(table.getByRole('row', { name: /Engineering/ }).first()).toBeVisible();
});
