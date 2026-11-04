import { expect, test } from '@playwright/test';

import { signIn } from './fake-sign-in';

const MANAGER = 'priya.nair@fernhill.example';
const FINANCE = 'lena.fischer@fernhill.example';

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
