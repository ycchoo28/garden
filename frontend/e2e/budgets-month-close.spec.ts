import { expect, test } from '@playwright/test';

import { signIn } from './fake-sign-in';

const FINANCE = 'lena.fischer@fernhill.example';
const MANAGER = 'priya.nair@fernhill.example';

// The database keeps its rows between runs, so each run closes its own month.
function runMonth(): string {
  const year = 2050 + Math.floor(Math.random() * 40);
  const month = 1 + Math.floor(Math.random() * 12);
  return `${year}-${String(month).padStart(2, '0')}`;
}

test('finance adds a budget line and it appears in the month', async ({ page }) => {
  await signIn(page, FINANCE, 'Lena Fischer');
  await page.goto('/budgets');
  const month = runMonth();
  await page.getByLabel('Month').fill(month);
  await page.getByLabel('Department').selectOption({ label: 'Design' });
  await page.getByLabel('Category').selectOption('software');
  await page.getByLabel('Amount (USD)').fill('1200.5');
  await page.getByRole('button', { name: `Add line for ${month}` }).click();

  const table = page.getByRole('table', { name: `Budget lines for ${month}` });
  await expect(table.getByRole('row', { name: /Design/ })).toContainText('1200.50');
});

test('finance closes a month in budgets and it is recorded', async ({ page }) => {
  await signIn(page, FINANCE, 'Lena Fischer');
  await page.goto('/budgets/months');
  const month = runMonth();
  await page.getByLabel('Month to close').fill(month);
  await page.getByLabel('Why close it?').fill('Claims for the month are approved');
  await page.getByRole('button', { name: 'Close month' }).click();

  await expect(page.getByRole('status')).toContainText(`Closed ${month}.`);
  await expect(page.getByText('Claims for the month are approved')).toBeVisible();
});

test('a manager cannot close a month', async ({ page }) => {
  await signIn(page, MANAGER, 'Priya Nair');
  await page.goto('/budgets/months');
  await expect(page.getByText('Only finance can close a month.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Close month' })).toHaveCount(0);
});
