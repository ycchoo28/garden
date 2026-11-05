<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query';
import { computed, ref } from 'vue';
import { useRouter } from 'vue-router';

import {
  claimsClaimCreateMutation,
  claimsClaimListOptions,
} from '../../../api/generated/@tanstack/vue-query.gen';
import { problemMessage } from '../../../shared/problem';
import { PageHeader, SectionCard } from '../../../shared/ui';
import ClaimStatus from '../ClaimStatus.vue';
import { formatAmount } from '../format';

const router = useRouter();
const queryClient = useQueryClient();
const { data: claims, isPending, isError, error } = useQuery(claimsClaimListOptions());

const title = ref('');
const create = useMutation({
  ...claimsClaimCreateMutation(),
  onSuccess: async (detail) => {
    await queryClient.invalidateQueries({ queryKey: claimsClaimListOptions().queryKey });
    await router.push({ name: 'claim', params: { id: detail.claim.id } });
  },
});

const canCreate = computed(() => title.value.trim().length > 0 && !create.isPending.value);

function startClaim(): void {
  if (!canCreate.value) return;
  create.mutate({ body: { title: title.value.trim() } });
}
</script>

<template>
  <PageHeader
    title="Claims"
    description="Your expense claims, and your department's claims if you manage one."
  />
  <div class="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
    <SectionCard
      title="Claims"
      flush
    >
      <p
        v-if="isPending"
        class="p-5 text-muted"
      >
        Loading claims.
      </p>
      <p
        v-else-if="isError"
        role="alert"
        class="p-5 text-danger-fg"
      >
        {{ problemMessage(error) }}
      </p>
      <p
        v-else-if="!claims?.length"
        class="p-5 text-muted"
      >
        No claims yet. Start one with the form.
      </p>
      <ul
        v-else
        class="divide-y divide-line"
      >
        <li
          v-for="claim in claims ?? []"
          :key="claim.id"
        >
          <RouterLink
            :to="{ name: 'claim', params: { id: claim.id } }"
            class="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-4 hover:bg-surface-hover"
          >
            <span class="min-w-0 flex-1">
              <span class="block truncate font-medium">{{ claim.title }}</span>
              <span class="block text-sm text-muted">
                {{ claim.claimant_name }} · {{ claim.department_code }}
              </span>
            </span>
            <ClaimStatus :status="claim.status" />
            <span class="w-28 text-right font-medium tabular-nums">{{ formatAmount(claim.total) }}</span>
          </RouterLink>
        </li>
      </ul>
    </SectionCard>

    <SectionCard
      title="New claim"
      description="Give the claim a name, then add its lines and receipts."
    >
      <form
        class="space-y-4"
        @submit.prevent="startClaim"
      >
        <label class="block">
          <span class="mb-1 block text-sm font-medium">Claim title</span>
          <input
            v-model="title"
            type="text"
            maxlength="200"
            required
            class="w-full rounded-lg border border-line-strong bg-surface px-3 py-2"
            placeholder="Client visit, Penang"
          >
        </label>
        <button
          type="submit"
          class="rounded-lg bg-primary px-4 py-2 font-medium text-primary-contrast hover:bg-primary-hover disabled:opacity-60"
          :disabled="!canCreate"
        >
          Create draft
        </button>
        <p
          v-if="create.isError.value"
          role="alert"
          class="text-sm text-danger-fg"
        >
          {{ problemMessage(create.error.value) }}
        </p>
      </form>
    </SectionCard>
  </div>
</template>
