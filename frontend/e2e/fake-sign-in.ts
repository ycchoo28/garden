import type { Page } from '@playwright/test';

import { OIDC_URL, WEB_URL } from './urls';

// Fills in the fake provider's own login page as a seeded person, the way a person does.
export async function signInAtProvider(page: Page, subject: string, name: string): Promise<void> {
  await page.waitForURL((url) => url.href.startsWith(OIDC_URL));
  await page.locator('input[name="username"]').fill(subject);
  await page.locator('textarea[name="claims"]').fill(JSON.stringify({ sub: subject, email: subject, name }));
  await page.getByRole('button', { name: 'Sign-in' }).click();
}

// Signs a seeded person in through the web app, so the browser holds a session for the pages.
export async function signIn(page: Page, subject: string, name: string): Promise<void> {
  await page.goto('/');
  await signInAtProvider(page, subject, name);
  await page.waitForURL(`${WEB_URL}/`);
}
