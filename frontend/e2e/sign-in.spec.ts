import { expect, test } from '@playwright/test';

import { signIn } from './fake-sign-in';
import { OIDC_URL, WEB_URL } from './urls';

const PERSON = 'dev.admin@fernhill.example';

test('the signed-out page is shown to a visitor with no session', async ({ page }) => {
  await page.goto('/signed-out');
  await expect(page.getByRole('heading', { name: 'You are signed out' })).toBeVisible();
});

test('a visitor with no session is sent to the fake sign-in provider', async ({ page }) => {
  await page.goto('/');
  await page.waitForURL((url) => url.href.startsWith(OIDC_URL));
  await expect(page.getByRole('button', { name: 'Dev Admin' })).toBeVisible();
});

test('a seeded person signs in and sees their name and permissions', async ({ page }) => {
  await signIn(page, PERSON, 'Dev Admin');

  await expect(page.getByRole('banner').getByText('Dev Admin')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Your permissions' })).toBeVisible();
  await expect(page.getByText('users.self', { exact: true })).toBeVisible();
  // The PrimeVue theme loads its colour variables even on a page with no PrimeVue component.
  const primary = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--p-primary-color'));
  expect(primary).not.toBe('');
});

test('the Garden name in the header goes back to the home page', async ({ page }) => {
  await signIn(page, PERSON, 'Dev Admin');
  await page.goto('/claims');
  await page.getByRole('banner').getByRole('link', { name: 'Garden' }).click();
  await page.waitForURL(`${WEB_URL}/`);
  await expect(page.getByRole('heading', { name: 'Your permissions' })).toBeVisible();
});

test('signing out ends the session and shows the signed-out page', async ({ page }) => {
  await signIn(page, PERSON, 'Dev Admin');
  await expect(page.getByRole('banner').getByText('Dev Admin')).toBeVisible();

  await page.getByRole('button', { name: 'Sign out' }).click();

  await expect(page).toHaveURL(`${WEB_URL}/signed-out`);
  await expect(page.getByRole('heading', { name: 'You are signed out' })).toBeVisible();
});
