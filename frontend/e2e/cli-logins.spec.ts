import { expect, test } from '@playwright/test';

import { signIn } from './fake-sign-in';
import { WEB_URL } from './urls';

const PERSON = 'dev.admin@fernhill.example';

test('a person creates a hosted agent login, sees its token once, and finds it in their sign-ins', async ({
  page,
}) => {
  await signIn(page, PERSON, 'Dev Admin');
  await page.goto(`${WEB_URL}/agent/logins`);
  await expect(page.getByRole('heading', { name: 'Agent logins' })).toBeVisible();

  const device = `e2e research agent ${Date.now()}`;
  await page.getByLabel('Name').fill(device);
  await page.getByLabel('Expires').selectOption({ label: 'In 30 days' });
  await page.getByRole('button', { name: 'Create Agent login' }).click();

  // The token is shown once, in a read-only field, with a warning that it will not be shown again.
  const token = page.getByRole('textbox', { name: 'New Agent login token' });
  await expect(token).toBeVisible();
  await expect(page.getByText('Copy this token now. It will not be shown again.')).toBeVisible();
  await expect(token).toHaveValue(/^ag_[A-Za-z0-9_-]+$/);

  // "I've saved it" clears the token from the page, and the form comes back for the next login.
  await page.getByRole('button', { name: "I've saved it" }).click();
  await expect(token).toHaveCount(0);
  await expect(page.getByText('Copy this token now. It will not be shown again.')).toHaveCount(0);
  await expect(page.getByLabel('Name')).toHaveValue('');

  // The login is listed with its source and expiry. A browser login says so, and a 30-day login expires.
  const row = page.getByRole('row').filter({ hasText: device });
  await expect(row).toHaveCount(1);
  const cells = row.getByRole('cell');
  await expect(cells.nth(0)).toHaveText(device);
  await expect(cells.nth(1)).toHaveText('Browser');
  await expect(cells.nth(3)).not.toHaveText('Never');
  await expect(cells.nth(5)).toHaveText('Active');
});
