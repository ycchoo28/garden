<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query';

import {
  usersCliLoginListOptions,
  usersCliLoginListQueryKey,
  usersCliLoginRevokeMutation,
} from '../../api/generated/@tanstack/vue-query.gen';
import { problemMessage } from '../../shared/problem';
import PageHeader from '../../shared/ui/PageHeader.vue';
import SectionCard from '../../shared/ui/SectionCard.vue';

const queryClient = useQueryClient();
const { data: logins, error, isPending } = useQuery(usersCliLoginListOptions());

// Only the person can revoke a CLI login. The command line keeps its token until this runs.
const revoke = useMutation({
  ...usersCliLoginRevokeMutation(),
  onSuccess: () => queryClient.invalidateQueries({ queryKey: usersCliLoginListQueryKey() }),
});

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
