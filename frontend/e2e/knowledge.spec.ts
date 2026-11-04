import { expect, test } from '@playwright/test';

import { signIn } from './fake-sign-in';
import { WEB_URL } from './urls';

// Seeded with the knowledge_admin role, so it may manage collections and batches.
const ADMIN = 'noor.haddad@fernhill.example';
// Seeded with no roles, so the knowledge pages are not for it.
const PLAIN = 'marcus.lee@fernhill.example';

test('a person without the knowledge role does not see the collection form', async ({ page }) => {
  await signIn(page, PLAIN, 'Marcus Lee');
  await page.goto('/collections');
  await expect(page.getByRole('heading', { name: 'Knowledge collections' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Create collection' })).toHaveCount(0);
});

test('an admin creates a collection, prepares a batch and previews it', async ({ page }) => {
  // A unique name, so a rerun against the same dev database does not collide with an earlier run.
  const name = `Retention ${Date.now()}`;

  await signIn(page, ADMIN, 'Noor Haddad');
  await page.goto('/collections');
  await expect(page.getByRole('heading', { name: 'Knowledge collections' })).toBeVisible();

  await page.getByLabel('Name').fill(name);
  await page.getByLabel('Role that may read it').fill('manager');
  await page.getByRole('button', { name: 'Create collection' }).click();

  const row = page.getByRole('listitem').filter({ hasText: name });
  await expect(row).toBeVisible();
  await row.getByRole('button', { name: 'Start an import batch' }).click();

  await expect(page).toHaveURL(new RegExp(`${WEB_URL}/batches/`));
  await expect(page.getByRole('heading', { name: 'Import batch' })).toBeVisible();

  await page.locator('input[name="file"]').setInputFiles({
    name: 'retention.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from('# Retention\n\nRecords are kept for seven years after the account closes.'),
  });
  await page.getByRole('button', { name: 'Add file' }).click();
  await expect(page.getByText('retention.md')).toBeVisible();

  await page.getByRole('button', { name: 'Prepare batch' }).click();
  await expect(page.getByText('State: prepared')).toBeVisible({ timeout: 60_000 });

  await page.getByRole('textbox', { name: 'Preview query' }).fill('records kept seven years');
  await page.getByRole('button', { name: 'Search' }).click();
  await expect(page.getByRole('article').filter({ hasText: 'Retention' })).toBeVisible();
});
