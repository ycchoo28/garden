import type { Page } from '@playwright/test';

import { OIDC_URL, WEB_URL } from './urls';

// Signs a seeded person in through the fake provider's own login page, the way a person does.
export async function signIn(page: Page, subject: string, name: string): Promise<void> {
  await page.goto('/');
  await page.waitForURL((url) => url.href.startsWith(OIDC_URL));
  await page.locator('input[name="username"]').fill(subject);
  await page.locator('textarea[name="claims"]').fill(JSON.stringify({ sub: subject, email: subject, name }));
  await page.getByRole('button', { name: 'Sign-in' }).click();
  await page.waitForURL(`${WEB_URL}/`);
}
