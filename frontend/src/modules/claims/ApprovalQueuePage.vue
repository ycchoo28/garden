<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query';

import { claimsDecisionQueueOptions } from '../../api/generated/@tanstack/vue-query.gen';
import { problemMessage } from '../../shared/problem';
import PageHeader from '../../shared/ui/PageHeader.vue';
import SectionCard from '../../shared/ui/SectionCard.vue';
import ClaimStatus from './ClaimStatus.vue';
import { formatAmount } from './format';

const { data: queue, isPending, isError, error } = useQuery(claimsDecisionQueueOptions());
</script>

<template>
  <PageHeader
    title="Approval queue"
    description="Claims waiting for your decision. You never see your own claims here."
  />
  <SectionCard
    title="Waiting for you"
    flush
  >
    <p
      v-if="isPending"
      class="p-5 text-muted"
    >
      Loading the queue.
    </p>
    <p
      v-else-if="isError"
      role="alert"
      class="p-5 text-danger-fg"
    >
      {{ problemMessage(error) }}
    </p>
    <p
      v-else-if="!queue?.length"
      class="p-5 text-muted"
    >
      Nothing is waiting for you.
    </p>
    <ul
      v-else
      class="divide-y divide-line"
    >
      <li
        v-for="claim in queue ?? []"
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
</template>
