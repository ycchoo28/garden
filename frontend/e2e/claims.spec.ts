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
