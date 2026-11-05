import { expect, test } from '@playwright/test';

import { signIn } from './fake-sign-in';
import { WEB_URL } from './urls';

const MANAGER = 'priya.nair@fernhill.example';
const FINANCE = 'lena.fischer@fernhill.example';

function thisMonth(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

// A fresh database has no budget lines, so finance sets one software budget per department this month
// first. A line from an earlier run already exists, which is fine.
test.beforeAll(async ({ browser }) => {
  const context = await browser.newContext({ baseURL: WEB_URL });
  const page = await context.newPage();
  await signIn(page, FINANCE, 'Lena Fischer');
  const departments: { id: string; code: string }[] = await (
    await page.request.get('/api/users/departments')
  ).json();
  for (const code of ['design', 'operations']) {
    const department = departments.find((item) => item.code === code);
    expect(department).toBeDefined();
    const response = await page.request.post('/api/budgets/lines', {
      data: {
        department_id: department?.id,
        year_month: thisMonth(),
        category: 'software',
        amount: '1200.00',
      },
    });
    expect([201, 409]).toContain(response.status());
  }
  await context.close();
});

test('a design manager sees only the design department on the variance page', async ({ page }) => {
  await signIn(page, MANAGER, 'Priya Nair');
  await page.goto('/budgets/variance');
  const table = page.getByRole('table', { name: /Budget variance from/ });
  await expect(table).toBeVisible();
  await expect(table.getByRole('row', { name: /Design/ }).first()).toBeVisible();
  await expect(table.getByRole('row', { name: /Operations/ })).toHaveCount(0);
});

test('finance sees every department on the variance page', async ({ page }) => {
  await signIn(page, FINANCE, 'Lena Fischer');
  await page.goto('/budgets/variance');
  const table = page.getByRole('table', { name: /Budget variance from/ });
  await expect(table).toBeVisible();
  await expect(table.getByRole('row', { name: /Operations/ }).first()).toBeVisible();
});
