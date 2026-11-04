import { type ChildProcess, spawn } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';

import { signIn, signInAtProvider } from './fake-sign-in';
import { API_URL, WEB_URL } from './urls';

const PERSON = 'dev.admin@fernhill.example';
// The garden command lives in the agent package. Running it through uv uses the same environment as `just setup`.
const AGENT_DIR = fileURLToPath(new URL('../../agent', import.meta.url));

interface Garden {
  child: ChildProcess;
  // Settles with the exit code, from the moment the process starts, so an early exit is never missed.
  exit: Promise<number | null>;
}

// Starts the garden command on this device's own home folder and a file secret store, never the keychain.
function garden(args: string[], env: NodeJS.ProcessEnv): Garden {
  const child = spawn('uv', ['run', '--project', AGENT_DIR, 'python', '-m', 'garden', ...args], {
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const exit = new Promise<number | null>((resolvePromise) => {
    child.on('exit', (code) => resolvePromise(code));
  });
  return { child, exit };
}

// Resolves with the first line that contains `marker` on one of the child's output streams.
function lineWith(child: ChildProcess, marker: string): Promise<string> {
  return new Promise((resolvePromise, reject) => {
    let stdout = '';
    let stderr = '';
    const find = (): void => {
      const line = `${stdout}\n${stderr}`.split('\n').find((text) => text.includes(marker));
      if (line) resolvePromise(line.trim());
    };
    child.stdout?.on('data', (chunk: Buffer) => {
      stdout += chunk.toString('utf8');
      find();
    });
    child.stderr?.on('data', (chunk: Buffer) => {
      stderr += chunk.toString('utf8');
      find();
    });
    child.on('exit', () => reject(new Error(`garden exited before it printed ${marker}`)));
  });
}

test('an agent deletes a file only after the person approves it in the browser', async ({ page }, testInfo) => {
  test.setTimeout(180_000);
  const home = testInfo.outputPath('garden-home');
  mkdirSync(home, { recursive: true });
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    GARDEN_HOME: home,
    GARDEN_API_URL: API_URL,
    GARDEN_SECRET_STORE: `file:${join(home, 'secrets.json')}`,
    PYTHONIOENCODING: 'utf-8',
  };

  // The person signs in to the web app, so this browser holds the session the approval page needs.
  await signIn(page, PERSON, 'Dev Admin');

  const upload = await page.request.post(`${WEB_URL}/api/files`, {
    multipart: {
      file: {
        name: 'agent-note.txt',
        mimeType: 'text/plain',
        buffer: Buffer.from(`a note the agent may delete ${Date.now()}`),
      },
    },
  });
  expect(upload.status()).toBe(200);
  const { sha256 } = (await upload.json()) as { sha256: string };

  // The device signs in through the same browser: the link opens the fake provider, and the person signs in there.
  const login = garden(['login', '--device', 'e2e-laptop', '--no-open'], env);
  try {
    const signInLink = await lineWith(login.child, '/api/cli-logins/authorize?');
    await page.goto(signInLink);
    await signInAtProvider(page, PERSON, 'Dev Admin');
    expect(await login.exit).toBe(0);

    // The delete is critical: the command waits, prints the approval link, and runs once the person approves.
    const remove = garden(['files', 'file', 'delete', '--sha256', sha256], env);
    try {
      const approvalLink = await lineWith(remove.child, '/confirm/');
      await page.goto(approvalLink);
      await expect(page.getByRole('heading', { name: 'Approve this action' })).toBeVisible();
      await expect(page.getByText('files.file.delete', { exact: true })).toBeVisible();
      await page.getByRole('button', { name: 'Approve' }).click();
      await expect(page.getByRole('status')).toContainText('You approved this action');

      expect(await remove.exit).toBe(0);
    } finally {
      remove.child.kill();
    }

    const after = await page.request.get(`${WEB_URL}/api/files/${sha256}`);
    expect(after.status()).toBe(404);
  } finally {
    login.child.kill();
  }
});
