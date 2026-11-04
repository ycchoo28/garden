<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query';
import { computed, ref, useId } from 'vue';

import { budgetsVarianceReadOptions } from '../../api/generated/@tanstack/vue-query.gen';
import { problemMessage } from '../../shared/problem';
import PageHeader from '../../shared/ui/PageHeader.vue';
import SectionCard from '../../shared/ui/SectionCard.vue';

const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;
const fieldClass =
  'rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm disabled:opacity-60';

function thisMonth(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

function firstMonthOfYear(): string {
  return `${new Date().getFullYear()}-01`;
}

const from = ref(firstMonthOfYear());
const to = ref(thisMonth());
const fromId = useId();
const toId = useId();
const rangeValid = computed(
  () => MONTH.test(from.value) && MONTH.test(to.value) && from.value <= to.value,
);

const variance = useQuery(
  computed(() => ({
    ...budgetsVarianceReadOptions({ query: { from: from.value, to: to.value } }),
    enabled: rangeValid.value,
  })),
);
const rows = computed(() => variance.data.value ?? []);
</script>

<template>
  <PageHeader
    title="Budget variance"
    description="Budget against actual spend by month and category. Actual is card spend plus approved claims. Managers see their own department."
  >
    <template #actions>
      <label
        :for="fromId"
        class="text-sm font-medium"
      >From</label>
      <input
        :id="fromId"
        v-model="from"
        type="month"
        :class="fieldClass"
      >
      <label
        :for="toId"
        class="text-sm font-medium"
      >To</label>
      <input
        :id="toId"
        v-model="to"
        type="month"
        :class="fieldClass"
      >
    </template>
  </PageHeader>

  <SectionCard
    title="Variance"
    description="Difference is budget less actual. A negative difference means spend went over budget."
    flush
  >
    <p
      v-if="!rangeValid"
      role="alert"
      class="m-5 rounded-lg border border-danger-line bg-danger-soft p-3 text-sm text-danger-fg"
    >
      Choose a valid range, with the From month not after the To month.
    </p>
    <p
      v-else-if="variance.isError.value"
      role="alert"
      class="m-5 rounded-lg border border-danger-line bg-danger-soft p-3 text-sm text-danger-fg"
    >
      {{ problemMessage(variance.error.value) }}
    </p>
    <p
      v-else-if="variance.isPending.value"
      class="p-5 text-sm text-muted"
    >
      Loading variance.
    </p>
    <p
      v-else-if="rows.length === 0"
      class="p-5 text-sm text-muted"
    >
      No budget or spend in this range.
    </p>
    <div
      v-else
      class="overflow-x-auto"
    >
      <table class="w-full text-left text-sm">
        <caption class="sr-only">
          Budget variance from {{ from }} to {{ to }}
        </caption>
        <thead class="bg-surface-muted text-muted">
          <tr>
            <th
              scope="col"
              class="px-5 py-2 font-medium"
            >
              Month
            </th>
            <th
              scope="col"
              class="px-5 py-2 font-medium"
            >
              Department
            </th>
            <th
              scope="col"
              class="px-5 py-2 font-medium"
            >
              Category
            </th>
            <th
              scope="col"
              class="px-5 py-2 text-right font-medium"
            >
              Budget (USD)
            </th>
            <th
              scope="col"
              class="px-5 py-2 text-right font-medium"
            >
              Actual (USD)
            </th>
            <th
              scope="col"
              class="px-5 py-2 text-right font-medium"
            >
              Difference (USD)
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="`${row.department_code}-${row.year_month}-${row.category}`"
            class="border-t border-line"
          >
            <td class="px-5 py-3 tabular-nums">
              {{ row.year_month }}
            </td>
            <td class="px-5 py-3">
              {{ row.department_name }}
            </td>
            <td class="px-5 py-3 capitalize">
              {{ row.category }}
            </td>
            <td class="px-5 py-3 text-right tabular-nums">
              {{ row.budget ?? 'No budget' }}
            </td>
            <td class="px-5 py-3 text-right tabular-nums">
              {{ row.actual }}
            </td>
            <td
              class="px-5 py-3 text-right tabular-nums"
              :class="row.variance?.startsWith('-') ? 'text-danger-fg' : ''"
            >
              {{ row.variance ?? '-' }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </SectionCard>
</template>
