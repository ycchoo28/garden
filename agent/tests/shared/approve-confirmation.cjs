// Drives a headless browser through one critical action's decision, the way a person would: signs in
// at the dev identity provider if asked, then approves or declines the action on the web app's page.
// Used by tests/shared/browser.py, through the end-to-end run in tests/e2e/run.py.
// Usage: node approve-confirmation.cjs <approval-link> <out-dir> <seeded-name> <seeded-subject> [approve|deny]
// Takes screenshots into out-dir and reads and writes no secrets. The browser never opens a window.
const path = require('node:path');
const { createRequire } = require('node:module');

// playwright-core comes from the frontend's dependencies, the same version the browser tests use.
const frontend = path.resolve(__dirname, '../../../frontend/package.json');
const { chromium } = createRequire(frontend)('playwright-core');

const [approvalLink, outDir, personName, personSubject, decision = 'approve'] = process.argv.slice(2);
const oidcUrl = process.env.GARDEN_E2E_OIDC_URL ?? 'http://127.0.0.1:55081';
const webUrl = new URL(approvalLink).origin;

(async () => {
  if (decision !== 'approve' && decision !== 'deny') {
    throw new Error(`Unknown confirmation decision: ${decision}.`);
  }
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
      let button;
      if (personName) {
        const named = page.getByRole('button', { name: personName });
        if (await named.count()) {
          const username = await named.locator('xpath=..').locator('input[name="username"]').inputValue();
          if (username === personSubject) button = named;
        }
      }
      if (!button) {
        for (const candidate of await page.getByRole('button').all()) {
          try {
            const username = await candidate.locator('xpath=..').locator('input[name="username"]').inputValue();
            if (username === personSubject) {
              button = candidate;
              break;
            }
          } catch {}
        }
      }
      if (!button) throw new Error(`The provider does not offer ${personSubject}.`);
      await button.click();
      await page.waitForURL((url) => url.href.startsWith(webUrl), { timeout: 60000 });
      console.log('signed in through the identity provider');
      await page.goto(approvalLink);
    }
    await heading.waitFor({ timeout: 30000 });
    await page.screenshot({ path: path.join(outDir, 'confirmation-before.png') });
    console.log('approval page opened');
    const label = decision === 'approve' ? 'Approve' : 'Decline';
    const outcome = decision === 'approve' ? 'You approved this action' : 'You declined this action';
    await page.getByRole('button', { name: label, exact: true }).click();
    await page.getByRole('status').filter({ hasText: outcome }).waitFor({ timeout: 30000 });
    console.log(`${decision === 'approve' ? 'approved' : 'declined'} in the browser`);
    await page.screenshot({ path: path.join(outDir, 'confirmation-after.png') });
    await context.close();
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
