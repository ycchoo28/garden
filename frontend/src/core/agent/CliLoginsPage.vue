<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query';

import {
  agentMyLoginListOptions,
  agentMyLoginListQueryKey,
  agentMyLoginSignOutMutation,
} from '../../api/generated/@tanstack/vue-query.gen';
import { problemMessage } from '../../shared/problem';
import { PageHeader, SectionCard } from '../../shared/ui';

const queryClient = useQueryClient();
const { data: logins, error, isPending } = useQuery(agentMyLoginListOptions());

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
</script>

<template>
  <PageHeader
    title="Command line sign-ins"
    description="Each row is a device where the garden command is signed in as you. Revoke a row to sign that device out."
  />
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
      No device is signed in. Run garden login in a terminal to add one.
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
              Signed in
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
              {{ when(login.created_at) }}
            </td>
            <td class="px-5 py-3">
              {{ when(login.last_used_at) }}
            </td>
            <td class="px-5 py-3">
              {{ login.revoked_at ? `Revoked ${when(login.revoked_at)}` : 'Active' }}
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
