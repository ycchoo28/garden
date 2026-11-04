import { expect, test } from '@playwright/test';

import { signIn } from './fake-sign-in';

const FINANCE = 'lena.fischer@fernhill.example';
const HEADER = 'transaction_ref,posted_on,department_code,category,merchant,amount_usd';

// The database keeps its rows between runs, so each run uses its own month and references.
function runMonth(): string {
  const year = 2050 + Math.floor(Math.random() * 40);
  const month = 1 + Math.floor(Math.random() * 12);
  return `${year}-${String(month).padStart(2, '0')}`;
}

function statementCsv(amount: string): Buffer {
  const ref = `e2e-${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
  return Buffer.from(`${HEADER}\n${ref},2026-09-14,design,software,Figma,${amount}\n`);
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

test('a statement with a bad row cannot be saved, and a clean one can', async ({ page }) => {
  await signIn(page, FINANCE, 'Lena Fischer');
  await page.goto('/spend');
  const input = page.getByLabel('Statement CSV');

  await input.setInputFiles({ name: 'bad.csv', mimeType: 'text/csv', buffer: statementCsv('12.345') });
  await page.getByRole('button', { name: 'Check statement' }).click();
  await expect(page.getByText('Nothing will be saved')).toBeVisible();
  await expect(page.getByRole('cell', { name: 'amount_usd' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save all rows' })).toBeDisabled();

  await input.setInputFiles({ name: 'good.csv', mimeType: 'text/csv', buffer: statementCsv('12.34') });
  await page.getByRole('button', { name: 'Check statement' }).click();
  await expect(page.getByText('Every row passes. You can save the whole statement.')).toBeVisible();
  await page.getByRole('button', { name: 'Save all rows' }).click();
  await expect(page.getByRole('status')).toContainText('Saved good.csv: 1 rows totalling 12.34 USD.');
});
