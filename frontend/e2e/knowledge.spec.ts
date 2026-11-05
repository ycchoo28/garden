import { expect, test } from "@playwright/test";

import { signIn } from "./fake-sign-in";
import { WEB_URL } from "./urls";

// Seeded with the admin role, so it may manage collections and decide batches.
const ADMIN = "dev.admin@fernhill.example";
// Seeded with the finance role, so it may start a batch but not decide one.
const STARTER = "lena.fischer@fernhill.example";
// Seeded with the employee role only, so the collection form is not for it.
const PLAIN = "marcus.lee@fernhill.example";

test("a person without the knowledge role does not see the collection form", async ({
  page,
}) => {
  await signIn(page, PLAIN, "Marcus Lee");
  await page.goto("/collections");
  await expect(
    page.getByRole("heading", { name: "Knowledge collections" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Create collection" }),
  ).toHaveCount(0);
});

test("an admin creates a collection, finance starts the batch, an admin approves it, and search finds it", async ({
  page,
}) => {
  // Preparing waits on the background worker, so this test needs more than the default 30s.
  test.setTimeout(120_000);
  // A unique name, so a rerun against the same dev database does not collide with an earlier run.
  const run = Date.now();
  const name = `Retention ${run}`;
  // The run number makes this passage, and a search for it, unique in a shared dev database.
  const passage = `Records are kept for seven years after the account closes. Ledger ${run}.`;

  await signIn(page, ADMIN, "Dev Admin");
  await page.goto("/collections");
  await expect(
    page.getByRole("heading", { name: "Knowledge collections" }),
  ).toBeVisible();

  await page.getByLabel("Name").fill(name);
  // Preview shows only what the caller may read, so the collection must grant a role the starter holds.
  await page.getByLabel("Role that may read it").fill("employee");
  await page.getByRole("button", { name: "Create collection" }).click();
  await expect(page.getByRole("listitem").filter({ hasText: name })).toBeVisible();

  // Finance starts the batch, because only finance and admin may upload it and only admin decides.
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(`${WEB_URL}/signed-out`);
  await signIn(page, STARTER, "Lena Fischer");
  await page.goto("/collections");
  const row = page.getByRole("listitem").filter({ hasText: name });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Start an import batch" }).click();

  await expect(page).toHaveURL(new RegExp(`${WEB_URL}/batches/`));
  await expect(
    page.getByRole("heading", { name: "Import batch" }),
  ).toBeVisible();
  const batchUrl = page.url();

  await page.locator('input[name="file"]').setInputFiles({
    name: "retention.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(`# Retention\n\n${passage}`),
  });
  await page.getByRole("button", { name: "Add file" }).click();
  await expect(page.getByText("retention.md")).toBeVisible();

  await page.getByRole("button", { name: "Prepare batch" }).click();
  await expect(page.getByText("State: prepared")).toBeVisible({
    timeout: 60_000,
  });

  await page
    .getByRole("textbox", { name: "Preview query" })
    .fill("records kept seven years");
  await page.getByRole("button", { name: "Search" }).click();
  await expect(
    page.getByRole("article").filter({ hasText: "Retention" }),
  ).toBeVisible();

  // The starter asks for approval. The staged result is fixed from here on.
  await page.getByRole("button", { name: "Ask for approval" }).click();
  await expect(page.getByText("State: awaiting approval")).toBeVisible();

  // The starter cannot decide their own batch, so they sign out and the admin signs in.
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(`${WEB_URL}/signed-out`);
  await signIn(page, ADMIN, "Dev Admin");
  await page.goto(batchUrl);
  await expect(page.getByText("State: awaiting approval")).toBeVisible();
  await page.getByRole("button", { name: "Approve and apply" }).click();
  await expect(page.getByText("State: applied")).toBeVisible();

  // Search reads the live content, so the applied passage is found.
  await page.goto("/search");
  await page
    .getByRole("textbox", { name: "Search query" })
    .fill(`records kept seven years ledger ${run}`);
  await page.getByRole("button", { name: "Search" }).click();
  const hit = page.getByRole("article").filter({ hasText: name });
  await expect(hit).toBeVisible();
  await expect(hit).toContainText(passage);
});
