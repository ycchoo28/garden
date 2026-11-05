// Drives a headless browser through one critical action's approval, the way a person would: signs in
// at the dev identity provider if asked, then approves the action on the web app's approval page.
// Used by scripts/e2e.py.
// Usage: node approve-confirmation.cjs <approval-link> <out-dir> <seeded-name> <seeded-subject>
// Takes screenshots into out-dir and reads and writes no secrets. The browser never opens a window.
const path = require('node:path');
const { createRequire } = require('node:module');

// playwright-core comes from the frontend's dependencies, the same version the browser tests use.
const frontend = path.resolve(__dirname, '../../frontend/package.json');
const { chromium } = createRequire(frontend)('playwright-core');

const [approvalLink, outDir, personName, personSubject] = process.argv.slice(2);
const oidcUrl = process.env.GARDEN_E2E_OIDC_URL ?? 'http://127.0.0.1:55081';
const webUrl = new URL(approvalLink).origin;

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const page = await context.newPage();
    await page.goto(approvalLink);
    const heading = page.getByRole('heading', { name: 'Approve this action' });
    // A browser with no session is sent to the provider, and comes back to the web app after the sign-in.
    const signedIn = await Promise.race([
      heading.waitFor({ timeout: 15000 }).then(() => true),
      page.waitForURL((url) => url.href.startsWith(oidcUrl), { timeout: 15000 }).then(() => false),
    ]);
    if (!signedIn) {
      const button = page.getByRole('button', { name: personName });
      await button.waitFor({ timeout: 30000 });
      const username = await button.locator('xpath=..').locator('input[name="username"]').inputValue();
      if (username !== personSubject) {
        throw new Error(`The provider offers ${username}, not ${personSubject}.`);
      }
      await button.click();
      await page.waitForURL((url) => url.href.startsWith(webUrl), { timeout: 60000 });
      console.log('signed in through the identity provider');
      await page.goto(approvalLink);
    }
    await heading.waitFor({ timeout: 30000 });
    await page.screenshot({ path: path.join(outDir, 'confirmation-before.png') });
    console.log('approval page opened');
    await page.getByRole('button', { name: 'Approve', exact: true }).click();
    await page.getByRole('status').filter({ hasText: 'You approved this action' }).waitFor({ timeout: 30000 });
    console.log('approved in the browser');
    await page.screenshot({ path: path.join(outDir, 'confirmation-after.png') });
    await context.close();
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
