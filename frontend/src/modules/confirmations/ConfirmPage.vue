<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query';
import { computed } from 'vue';
import { useRoute } from 'vue-router';

import {
  confirmationsRequestDecideMutation,
  confirmationsRequestReadOptions,
  confirmationsRequestReadQueryKey,
} from '../../api/generated/@tanstack/vue-query.gen';
import { problemMessage } from '../../shared/problem';
import PageHeader from '../../shared/ui/PageHeader.vue';
import SectionCard from '../../shared/ui/SectionCard.vue';

// The page a person opens from the garden command. It shows the request and waits for their decision.
const route = useRoute();
const confirmationId = String(route.params.id);
const path = { confirmation_id: confirmationId };

const queryClient = useQueryClient();
const { data: confirmation, error, isPending } = useQuery(
  confirmationsRequestReadOptions({ path }),
);

const decide = useMutation({
  ...confirmationsRequestDecideMutation(),
  onSuccess: (view) => {
    queryClient.setQueryData(confirmationsRequestReadQueryKey({ path }), view);
  },
});

const pending = computed(() => confirmation.value?.status === 'pending');

const outcome: Record<string, string> = {
  approved: 'You approved this action. The command can now run it.',
  rejected: 'You declined this action. It did not run.',
  expired: 'This request expired before anyone decided. Run the command again to ask once more.',
  used: 'You approved this action and it has run.',
};

function answer(decision: 'approve' | 'reject'): void {
  decide.mutate({ path, body: { decision } });
}

function when(value: string): string {
  return new Date(value).toLocaleString();
}
</script>

<template>
  <PageHeader
    title="Approve this action"
    description="The garden command asked to run this for you. Nothing runs until you approve it here."
  />
  <p
    v-if="isPending"
    class="text-muted"
  >
    Loading the request.
  </p>
  <p
    v-else-if="error"
    role="alert"
    class="text-danger"
  >
    {{ problemMessage(error) }}
  </p>
  <SectionCard
    v-else-if="confirmation"
    title="The request"
  >
    <dl class="grid grid-cols-1 gap-4 sm:grid-cols-[10rem_1fr]">
      <dt class="text-sm text-muted">
        Action
      </dt>
      <dd class="font-mono text-sm break-all">
        {{ confirmation.operation }}
      </dd>
      <dt class="text-sm text-muted">
        Request
      </dt>
      <dd class="font-mono text-sm break-all">
        {{ confirmation.method }} {{ confirmation.target }}
      </dd>
      <dt class="text-sm text-muted">
        Details
      </dt>
      <dd class="whitespace-pre-wrap break-words text-sm">
        {{ confirmation.summary ?? 'No details were given.' }}
      </dd>
      <dt class="text-sm text-muted">
        Decide by
      </dt>
      <dd class="text-sm">
        {{ when(confirmation.expires_at) }}
      </dd>
    </dl>

    <div
      v-if="pending"
      class="mt-6 flex flex-wrap gap-3"
    >
      <button
        type="button"
        class="rounded-lg bg-primary px-4 py-2 font-semibold text-primary-contrast disabled:opacity-60"
        :disabled="decide.isPending.value"
        @click="answer('approve')"
      >
        Approve
      </button>
      <button
        type="button"
        class="rounded-lg border border-line px-4 py-2 font-semibold disabled:opacity-60"
        :disabled="decide.isPending.value"
        @click="answer('reject')"
      >
        Decline
      </button>
    </div>
    <p
      v-else
      role="status"
      class="mt-6 text-sm"
    >
      {{ outcome[confirmation.status] ?? `This request is ${confirmation.status}.` }}
    </p>
    <p
      v-if="decide.error.value"
      role="alert"
      class="mt-4 text-sm text-danger"
    >
      {{ problemMessage(decide.error.value) }}
    </p>
  </SectionCard>
</template>
