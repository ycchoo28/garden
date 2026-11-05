<script setup lang="ts">
import { useMutation, useQuery } from '@tanstack/vue-query';
import { computed, ref, useId } from 'vue';

import {
  budgetsMonthCloseMutation,
  budgetsMonthListOptions,
  usersSelfReadOptions,
} from '../../../api/generated/@tanstack/vue-query.gen';
import { problemFieldMessages, problemMessage } from '../../../shared/problem';
import { PageHeader, SectionCard } from '../../../shared/ui';
import { BUDGETS_MONTH_CLOSE_PERMISSION } from '../access';

const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;
const fieldClass =
  'w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm disabled:opacity-60';
const buttonClass =
  'rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-contrast hover:bg-primary-hover disabled:opacity-60';

const { data: me } = useQuery(usersSelfReadOptions());
const canClose = computed(() => me.value?.permissions.includes(BUDGETS_MONTH_CLOSE_PERMISSION) ?? false);

const months = useQuery({ ...budgetsMonthListOptions(), enabled: canClose });
const closedMonths = computed(() => months.data.value ?? []);

const closeMonthId = useId();
const closeMonth = ref('');
const closeReason = ref('');
const closeError = ref('');
const closeNote = ref('');
const close = useMutation(budgetsMonthCloseMutation());

async function runClose(): Promise<void> {
  closeError.value = '';
  closeNote.value = '';
  try {
    const closed = await close.mutateAsync({
      path: { year_month: closeMonth.value },
      body: { reason: closeReason.value },
    });
    closeNote.value = `Closed ${closed.year_month}. Its claim lines can no longer change.`;
    closeMonth.value = '';
    closeReason.value = '';
    await months.refetch();
  } catch (error) {
    closeError.value = [problemMessage(error), ...problemFieldMessages(error)].join(' ');
  }
}

const closeReady = computed(() => MONTH.test(closeMonth.value) && closeReason.value.trim() !== '');

function shortTime(value: string): string {
  return value.replace('T', ' ').slice(0, 16);
}
</script>

<template>
  <PageHeader
    title="Month close"
    description="Finance closes a month once its claims are complete. A closed month never reopens."
  />

  <div class="grid gap-6">
    <SectionCard
      title="Closed months"
      description="After a month is closed, no claim line for it can be added or changed."
    >
      <p
        v-if="!canClose"
        class="text-sm text-muted"
      >
        Only finance can close a month.
      </p>
      <template v-else>
        <ul
          v-if="closedMonths.length"
          class="mb-5 space-y-2 text-sm"
        >
          <li
            v-for="closed in closedMonths"
            :key="closed.year_month"
          >
            <span class="font-semibold tabular-nums">{{ closed.year_month }}</span>
            <span class="text-muted">
              closed {{ shortTime(closed.closed_at) }}: {{ closed.reason }}
            </span>
          </li>
        </ul>
        <p
          v-else
          class="mb-5 text-sm text-muted"
        >
          No month is closed yet.
        </p>

        <form
          class="grid gap-4 sm:grid-cols-[10rem_1fr_auto] sm:items-end"
          @submit.prevent="runClose"
        >
          <label
            :for="closeMonthId"
            class="grid gap-1 text-sm font-medium"
          >
            Month to close
            <input
              :id="closeMonthId"
              v-model="closeMonth"
              type="month"
              :class="fieldClass"
              required
            >
          </label>
          <label class="grid gap-1 text-sm font-medium">
            Why close it?
            <input
              v-model="closeReason"
              :class="fieldClass"
              maxlength="500"
              required
            >
          </label>
          <button
            type="submit"
            :class="buttonClass"
            :disabled="!closeReady || close.isPending.value"
          >
            Close month
          </button>
          <p
            v-if="closeError"
            role="alert"
            class="text-sm text-danger-fg sm:col-span-3"
          >
            {{ closeError }}
          </p>
          <p
            v-if="closeNote"
            role="status"
            class="text-sm text-success-fg sm:col-span-3"
          >
            {{ closeNote }}
          </p>
        </form>
      </template>
    </SectionCard>
  </div>
</template>
