import { expect, test } from '@playwright/test';

import { signIn } from './fake-sign-in';
import { WEB_URL } from './urls';

// Seeded with the knowledge_admin role, so it may manage collections and batches.
const ADMIN = 'noor.haddad@fernhill.example';
// Seeded with the knowledge_admin role too. A batch must be approved by someone other than its starter.
const APPROVER = 'dev.admin@fernhill.example';
// Seeded with no roles, so the knowledge pages are not for it.
const PLAIN = 'marcus.lee@fernhill.example';

test('a person without the knowledge role does not see the collection form', async ({ page }) => {
  await signIn(page, PLAIN, 'Marcus Lee');
  await page.goto('/collections');
  await expect(page.getByRole('heading', { name: 'Knowledge collections' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Create collection' })).toHaveCount(0);
});

test('an admin creates a collection, another admin approves the batch, and search finds it', async ({ page }) => {
  // A unique name, so a rerun against the same dev database does not collide with an earlier run.
  const name = `Retention ${Date.now()}`;
  const passage = 'Records are kept for seven years after the account closes.';

  await signIn(page, ADMIN, 'Noor Haddad');
  await page.goto('/collections');
  await expect(page.getByRole('heading', { name: 'Knowledge collections' })).toBeVisible();

  await page.getByLabel('Name').fill(name);
  await page.getByLabel('Role that may read it').fill('knowledge_admin');
  await page.getByRole('button', { name: 'Create collection' }).click();

  const row = page.getByRole('listitem').filter({ hasText: name });
  await expect(row).toBeVisible();
  await row.getByRole('button', { name: 'Start an import batch' }).click();

  await expect(page).toHaveURL(new RegExp(`${WEB_URL}/batches/`));
  await expect(page.getByRole('heading', { name: 'Import batch' })).toBeVisible();
  const batchUrl = page.url();

  await page.locator('input[name="file"]').setInputFiles({
    name: 'retention.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from(`# Retention\n\n${passage}`),
  });
  await page.getByRole('button', { name: 'Add file' }).click();
  await expect(page.getByText('retention.md')).toBeVisible();

  await page.getByRole('button', { name: 'Prepare batch' }).click();
  await expect(page.getByText('State: prepared')).toBeVisible({ timeout: 60_000 });

  await page.getByRole('textbox', { name: 'Preview query' }).fill('records kept seven years');
  await page.getByRole('button', { name: 'Search' }).click();
  await expect(page.getByRole('article').filter({ hasText: 'Retention' })).toBeVisible();

  // The starter asks for approval. The staged result is fixed from here on.
  await page.getByRole('button', { name: 'Ask for approval' }).click();
  await expect(page.getByText('State: awaiting approval')).toBeVisible();

  // The starter cannot decide their own batch, so the approver signs in and applies it.
  await signIn(page, APPROVER, 'Dev Admin');
  await page.goto(batchUrl);
  await expect(page.getByText('State: awaiting approval')).toBeVisible();
  await page.getByRole('button', { name: 'Approve and apply' }).click();
  await expect(page.getByText('State: applied')).toBeVisible();

  // Search reads the live content, so the applied passage is found.
  await page.goto('/search');
  await page.getByRole('textbox', { name: 'Search query' }).fill('records kept seven years');
  await page.getByRole('button', { name: 'Search' }).click();
  const hit = page.getByRole('article').filter({ hasText: passage });
  await expect(hit).toBeVisible();
  await expect(hit).toContainText('Retention');
});
