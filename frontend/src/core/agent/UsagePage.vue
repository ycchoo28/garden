<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query';
import { computed } from 'vue';

import { agentMyModelUsageReadOptions } from '../../api/generated/@tanstack/vue-query.gen';
import { problemMessage } from '../../shared/problem';
import { PageHeader, SectionCard } from '../../shared/ui';

// Counts only: the gateway keeps how many requests and tokens you used, never the prompts or replies.
const { data: usage, error, isPending } = useQuery(agentMyModelUsageReadOptions());

const share = computed(() => {
  const today = usage.value;
  if (!today || today.tokens_limit <= 0) return 0;
  return Math.min(100, Math.round((today.total_tokens / today.tokens_limit) * 100));
});
</script>

<template>
  <PageHeader
    title="Model usage today"
    description="How much of your daily model allowance the gateway has used. The day resets at midnight UTC."
  />
  <SectionCard
    title="Today"
    :description="usage ? `Usage for ${usage.day} (UTC)` : undefined"
  >
    <p
      v-if="isPending"
      class="text-muted"
    >
      Loading today's usage.
    </p>
    <p
      v-else-if="error"
      role="alert"
      class="text-danger"
    >
      {{ problemMessage(error) }}
    </p>
    <div
      v-else-if="usage"
      class="space-y-6"
    >
      <div>
        <div class="flex flex-wrap items-baseline justify-between gap-2">
          <span class="text-sm text-muted">Tokens used</span>
          <span class="font-semibold tabular-nums">
            {{ usage.total_tokens.toLocaleString() }} of {{ usage.tokens_limit.toLocaleString() }}
          </span>
        </div>
        <div
          class="mt-2 h-2 overflow-hidden rounded-full bg-surface-muted"
          role="meter"
          aria-label="Share of today's token allowance used"
          :aria-valuenow="share"
          aria-valuemin="0"
          aria-valuemax="100"
        >
          <div
            class="h-full rounded-full bg-primary"
            :style="{ width: `${share}%` }"
          />
        </div>
      </div>
      <dl class="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div>
          <dt class="text-sm text-muted">
            Requests
          </dt>
          <dd class="text-xl font-semibold tabular-nums">
            {{ usage.requests.toLocaleString() }}
          </dd>
        </div>
        <div>
          <dt class="text-sm text-muted">
            Prompt tokens
          </dt>
          <dd class="text-xl font-semibold tabular-nums">
            {{ usage.prompt_tokens.toLocaleString() }}
          </dd>
        </div>
        <div>
          <dt class="text-sm text-muted">
            Completion tokens
          </dt>
          <dd class="text-xl font-semibold tabular-nums">
            {{ usage.completion_tokens.toLocaleString() }}
          </dd>
        </div>
      </dl>
    </div>
  </SectionCard>
</template>
