import { expect, test } from '@playwright/test';

import { signIn } from './fake-sign-in';
import { WEB_URL } from './urls';

const EMPLOYEE = 'marcus.lee@fernhill.example';
const MANAGER = 'priya.nair@fernhill.example';

// The first bytes of a PNG file. The API checks the file's signature, so this is a valid receipt for a test.
const RECEIPT = {
  name: 'receipt.png',
  mimeType: 'image/png',
  buffer: Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(16)]),
};

test('an employee submits a claim and their manager approves it', async ({ page }) => {
  const title = `Client visit ${Date.now()}`;

  await signIn(page, EMPLOYEE, 'Marcus Lee');
  await page.getByRole('link', { name: 'Claims' }).click();
  await page.getByLabel('Claim title').fill(title);
  await page.getByRole('button', { name: 'Create draft' }).click();
  await expect(page.getByRole('heading', { name: title, level: 1 })).toBeVisible();

  await page.getByLabel('Date spent').fill('2026-10-12');
  await page.getByLabel('Amount').fill('120.00');
  await page.getByLabel('Description').fill('Train to the client site');
  await page.getByLabel('Receipt').setInputFiles(RECEIPT);
  await page.getByRole('button', { name: 'Save line' }).click();
  await expect(page.getByRole('cell', { name: 'Train to the client site', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: /View receipt/ }).first()).toBeVisible();

  await page.getByRole('checkbox', { name: /I certify/ }).check();
  await page.getByRole('button', { name: 'Certify and submit' }).click();
  await expect(page.getByRole('button', { name: 'Withdraw claim' })).toBeVisible();

  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page).toHaveURL(`${WEB_URL}/signed-out`);

  await signIn(page, MANAGER, 'Priya Nair');
  await page.getByRole('link', { name: 'Approvals' }).click();
  await page.getByRole('link', { name: title }).click();
  await page.getByRole('button', { name: 'Approve' }).click();
  await expect(page.getByText('Approved', { exact: true }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Approve' })).toHaveCount(0);
});

test('a claim line uses API categories and keeps its category after a client-side remount', async ({ page }) => {
  const title = `API categories ${Date.now()}`;
  let categoryRequests = 0;

  await signIn(page, EMPLOYEE, 'Marcus Lee');
  await page.route('**/api/budgets/categories', async (route) => {
    categoryRequests += 1;
    await route.fulfill({ json: ['travel'] });
  });
  await page.getByRole('link', { name: 'Claims' }).click();
  await page.getByLabel('Claim title').fill(title);
  await page.getByRole('button', { name: 'Create draft' }).click();
  await expect(page.getByRole('heading', { name: title, level: 1 })).toBeVisible();

  const category = page.getByLabel('Category');
  await expect(category.locator('option')).toHaveText(['Travel']);
  await expect(category).toHaveValue('travel');
  expect(categoryRequests).toBeGreaterThan(0);

  await page.getByRole('link', { name: 'Claims' }).click();
  await page.getByRole('link', { name: title }).click();
  await expect(category.locator('option')).toHaveText(['Travel']);
  await expect(category).toHaveValue('travel');
});

test('a category API failure leaves claim lines unavailable without a fallback list', async ({ page }) => {
  const title = `Unavailable categories ${Date.now()}`;

  await signIn(page, EMPLOYEE, 'Marcus Lee');
  await page.route('**/api/budgets/categories', async (route) => {
    await route.fulfill({
      status: 500,
      contentType: 'application/problem+json',
      body: JSON.stringify({ title: 'Category service unavailable' }),
    });
  });
  await page.getByRole('link', { name: 'Claims' }).click();
  await page.getByLabel('Claim title').fill(title);
  await page.getByRole('button', { name: 'Create draft' }).click();
  await expect(page.getByRole('heading', { name: title, level: 1 })).toBeVisible();

  const category = page.getByLabel('Category');
  await expect(
    page.getByRole('alert').filter({ hasText: 'Category service unavailable' }),
  ).toBeVisible({ timeout: 20_000 });
  await expect(category).toBeDisabled();
  await expect(category.locator('option')).toHaveCount(0);

  await page.getByLabel('Date spent').fill('2026-10-12');
  await page.getByLabel('Amount').fill('120.00');
  await page.getByLabel('Description').fill('Train to the client site');
  await page.getByLabel('Receipt').setInputFiles(RECEIPT);
  await expect(page.getByRole('button', { name: 'Save line' })).toBeDisabled();
});
