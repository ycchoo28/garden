import { expect, type Page } from '@playwright/test';

import { OIDC_URL, WEB_URL } from './urls';

// Picks a seeded person on the fake provider's own login page, the way a person does.
export async function signInAtProvider(page: Page, subject: string, name: string): Promise<void> {
  await page.waitForURL((url) => url.href.startsWith(OIDC_URL));
  const button = page.getByRole('button', { name });
  await expect(button.locator('xpath=..').locator('input[name="username"]')).toHaveValue(subject);
  await button.click();
}

// Signs a seeded person in through the web app, so the browser holds a session for the pages.
export async function signIn(page: Page, subject: string, name: string): Promise<void> {
  await page.goto('/');
  await signInAtProvider(page, subject, name);
  await page.waitForURL(`${WEB_URL}/`);
}
