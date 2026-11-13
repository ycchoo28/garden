// Drives a headless browser through a CLI sign-in, the way a person would: the browser signs in at the
// dev identity provider as a seeded person, and the CLI's loopback listener receives the approval.
// Used by tests/shared/browser.py, through the end-to-end run in tests/e2e/run.py.
// Usage: node approve-login.cjs <login-link> <out-dir> <seeded-name> <seeded-subject>
// Takes one screenshot into out-dir and reads and writes no secrets. The browser never opens a window.
const path = require('node:path');
const { createRequire } = require('node:module');

// playwright-core comes from the frontend's dependencies, the same version the browser tests use.
const frontend = path.resolve(__dirname, '../../../frontend/package.json');
const { chromium } = createRequire(frontend)('playwright-core');

const [loginLink, outDir, personName, personSubject] = process.argv.slice(2);
const oidcUrl = process.env.GARDEN_E2E_OIDC_URL ?? 'http://127.0.0.1:55081';

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const page = await context.newPage();
    await page.goto(loginLink);
    await page.waitForURL((url) => url.href.startsWith(oidcUrl), { timeout: 60000 });
    // The provider's sign-in page: each seeded person has a button, and the username field beside it.
    // The button's name also carries the person's role, so the name matches without `exact`.
    const button = page.getByRole('button', { name: personName });
    await button.waitFor({ timeout: 30000 });
    const username = await button.locator('xpath=..').locator('input[name="username"]').inputValue();
    if (username !== personSubject) {
      throw new Error(`The provider offers ${username}, not ${personSubject}.`);
    }
    console.log('signed in through the identity provider');
    await button.click();
    // The provider returns the code to the CLI's listener on this machine, which answers with its page.
    await page.waitForURL(/^http:\/\/127\.0\.0\.1:\d+\/callback/, { timeout: 60000 });
    await page.getByRole('heading', { name: 'Sign-in succeeded' }).waitFor({ timeout: 30000 });
    console.log('approved; the CLI received the login');
    await page.screenshot({ path: path.join(outDir, 'login-approved.png') });
    await context.close();
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
