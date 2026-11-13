<script setup lang="ts">
import { ref } from 'vue';
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query';

import {
  agentMyLoginListOptions,
  agentMyLoginListQueryKey,
  agentMyLoginCreateMutation,
  agentMyLoginSignOutMutation,
} from '../../api/generated/@tanstack/vue-query.gen';
import type { CliLoginCreated } from '../../api/generated/types.gen';
import { problemMessage } from '../../shared/problem';
import { PageHeader, SectionCard } from '../../shared/ui';

const queryClient = useQueryClient();
const { data: logins, error, isPending } = useQuery(agentMyLoginListOptions());
const device = ref('');
const expiresInDays = ref<30 | 90 | 365 | null>(90);
const createdLogin = ref<CliLoginCreated | null>(null);
const copyError = ref('');

const create = useMutation({
  ...agentMyLoginCreateMutation(),
  onSuccess: (login) => {
    createdLogin.value = login;
    device.value = '';
    queryClient.invalidateQueries({ queryKey: agentMyLoginListQueryKey() });
  },
});

// Only the person can revoke a CLI login. The command line keeps its token until this runs.
const revoke = useMutation({
  ...agentMyLoginSignOutMutation(),
  onSuccess: () => queryClient.invalidateQueries({ queryKey: agentMyLoginListQueryKey() }),
});

// The installer and dsh come from this portal, so the commands name the origin the person opened.
const origin = window.location.origin;

function when(value: string | null): string {
  return value ? new Date(value).toLocaleString() : 'Never';
}

function status(login: { revoked_at: string | null; expires_at: string | null }): string {
  if (login.revoked_at) return `Revoked ${when(login.revoked_at)}`;
  if (login.expires_at && Date.parse(login.expires_at) <= Date.now()) {
    return `Expired ${when(login.expires_at)}`;
  }
  return 'Active';
}

async function copyToken(): Promise<void> {
  copyError.value = '';
  try {
    await navigator.clipboard.writeText(createdLogin.value?.token ?? '');
  } catch {
    copyError.value = 'Copy failed. Select and copy the token above.';
  }
}

function dismissToken(): void {
  createdLogin.value = null;
  create.reset();
}
</script>

<template>
  <PageHeader
    title="Agent logins"
    description="Manage Agent logins for hosted agents and the garden command. Revoke a row to stop that token."
  />
  <SectionCard
    title="New Agent login"
    class="mb-6"
  >
    <form
      v-if="!createdLogin"
      class="grid gap-4 sm:max-w-xl"
      @submit.prevent="create.mutate({ body: { device, expires_in_days: expiresInDays } })"
    >
      <label class="grid gap-1 text-sm font-medium">
        Name
        <input
          v-model="device"
          required
          maxlength="120"
          autocomplete="off"
          class="rounded-lg border border-line bg-surface px-3 py-2 font-normal"
          placeholder="For example, hosted research agent"
        >
      </label>
      <label class="grid gap-1 text-sm font-medium">
        Expires
        <select
          v-model="expiresInDays"
          class="w-fit rounded-lg border border-line bg-surface px-3 py-2 font-normal"
        >
          <option :value="30">In 30 days</option>
          <option :value="90">In 90 days</option>
          <option :value="365">In 365 days</option>
          <option :value="null">Never</option>
        </select>
      </label>
      <button
        type="submit"
        class="w-fit rounded-lg bg-primary px-4 py-2 font-medium text-primary-contrast disabled:opacity-60"
        :disabled="create.isPending.value || !device.trim()"
      >
        {{ create.isPending.value ? 'Creating…' : 'Create Agent login' }}
      </button>
      <p
        v-if="create.error.value"
        role="alert"
        class="text-danger"
      >
        {{ problemMessage(create.error.value) }}
      </p>
    </form>
    <div
      v-else
      class="grid gap-3 sm:max-w-2xl"
    >
      <p class="font-medium">
        Copy this token now. It will not be shown again.
      </p>
      <input
        :value="createdLogin.token"
        readonly
        aria-label="New Agent login token"
        class="w-full rounded-lg border border-line bg-surface-muted px-3 py-2 font-mono text-sm"
      >
      <p class="text-sm text-muted">
        Store it as a secret in your hosted agent platform. Anyone with this token can act within your agent permissions.
      </p>
      <p
        v-if="copyError"
        role="alert"
        class="text-danger"
      >
        {{ copyError }}
      </p>
      <div class="flex flex-wrap gap-2">
        <button
          type="button"
          class="rounded-lg border border-line px-4 py-2 font-medium"
          @click="copyToken"
        >
          Copy token
        </button>
        <button
          type="button"
          class="rounded-lg border border-line px-4 py-2 font-medium"
          @click="dismissToken"
        >
          I've saved it
        </button>
      </div>
    </div>
  </SectionCard>
  <SectionCard
    title="Get started"
    class="mb-6"
  >
    <ol class="grid list-inside list-decimal gap-3 text-sm">
      <li>
        Install the garden command and dsh. On macOS or Linux, run this in a terminal:
        <code class="mt-2 block overflow-x-auto whitespace-pre rounded bg-surface-muted px-3 py-2 font-mono text-sm">curl -fsSL {{ origin }}/api/agent/downloads/install.sh | GARDEN_API_URL={{ origin }} bash</code>
        On Windows, run this in PowerShell:
        <code class="mt-2 block overflow-x-auto whitespace-pre rounded bg-surface-muted px-3 py-2 font-mono text-sm">$env:GARDEN_API_URL = '{{ origin }}'; irm {{ origin }}/api/agent/downloads/install.ps1 | iex</code>
      </li>
      <li>
        The installer signs you in last and opens a browser page to approve it. If that step did not finish, run this and approve the login in the browser:
        <code class="mt-2 block overflow-x-auto whitespace-pre rounded bg-surface-muted px-3 py-2 font-mono text-sm">garden login</code>
      </li>
      <li>
        Start dsh, the coding agent, signed in as you:
        <code class="mt-2 block overflow-x-auto whitespace-pre rounded bg-surface-muted px-3 py-2 font-mono text-sm">garden dsh</code>
      </li>
    </ol>
  </SectionCard>
  <SectionCard
    title="Your sign-ins"
    flush
  >
    <p
      v-if="isPending"
      class="p-5 text-muted"
    >
      Loading your sign-ins.
    </p>
    <p
      v-else-if="error"
      role="alert"
      class="p-5 text-danger"
    >
      {{ problemMessage(error) }}
    </p>
    <p
      v-else-if="!logins?.length"
      class="p-5 text-muted"
    >
      No Agent logins yet. Create one here for a hosted agent or run garden login in a terminal.
    </p>
    <div
      v-else
      class="overflow-x-auto"
    >
      <table class="w-full text-left text-sm">
        <thead class="border-b border-line text-muted">
          <tr>
            <th
              scope="col"
              class="px-5 py-3 font-medium"
            >
              Device
            </th>
            <th
              scope="col"
              class="px-5 py-3 font-medium"
            >
              Source
            </th>
            <th
              scope="col"
              class="px-5 py-3 font-medium"
            >
              Signed in
            </th>
            <th
              scope="col"
              class="px-5 py-3 font-medium"
            >
              Expires
            </th>
            <th
              scope="col"
              class="px-5 py-3 font-medium"
            >
              Last used
            </th>
            <th
              scope="col"
              class="px-5 py-3 font-medium"
            >
              Status
            </th>
            <th
              scope="col"
              class="px-5 py-3"
            >
              <span class="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="login in logins"
            :key="login.id"
            class="border-b border-line last:border-b-0"
          >
            <td class="px-5 py-3 font-medium">
              {{ login.device }}
            </td>
            <td class="px-5 py-3">
              {{ login.source === 'web' ? 'Browser' : 'garden command' }}
            </td>
            <td class="px-5 py-3">
              {{ when(login.created_at) }}
            </td>
            <td class="px-5 py-3">
              {{ when(login.expires_at) }}
            </td>
            <td class="px-5 py-3">
              {{ when(login.last_used_at) }}
            </td>
            <td class="px-5 py-3">
              {{ status(login) }}
            </td>
            <td class="px-5 py-3 text-right">
              <button
                v-if="!login.revoked_at"
                type="button"
                class="rounded-lg border border-line px-3 py-1.5 font-medium disabled:opacity-60"
                :disabled="revoke.isPending.value"
                @click="revoke.mutate({ path: { login_id: login.id } })"
              >
                Revoke {{ login.device }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p
      v-if="revoke.error.value"
      role="alert"
      class="border-t border-line px-5 py-3 text-danger"
    >
      {{ problemMessage(revoke.error.value) }}
    </p>
  </SectionCard>
</template>
