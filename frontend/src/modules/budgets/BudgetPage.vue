<script setup lang="ts">
import { useMutation, useQuery } from '@tanstack/vue-query';
import { computed, ref, useId } from 'vue';

import {
  budgetsCategoryListOptions,
  budgetsLineCreateMutation,
  budgetsLineListOptions,
  budgetsLineReplaceMutation,
  usersDepartmentListOptions,
  usersSelfReadOptions,
} from '../../api/generated/@tanstack/vue-query.gen';
import type { LineOut } from '../../api/generated/types.gen';
import { problemFieldMessages, problemMessage } from '../../shared/problem';
import PageHeader from '../../shared/ui/PageHeader.vue';
import SectionCard from '../../shared/ui/SectionCard.vue';

const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;
const fieldClass =
  'w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm disabled:opacity-60';
const buttonClass =
  'rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-contrast hover:bg-primary-hover disabled:opacity-60';

function thisMonth(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

const { data: me } = useQuery(usersSelfReadOptions());
const canManage = computed(() => me.value?.permissions.includes('budgets.manage') ?? false);

const month = ref(thisMonth());
const monthId = useId();
const lines = useQuery(
  computed(() => ({
    ...budgetsLineListOptions({ query: { month: month.value } }),
    enabled: MONTH.test(month.value),
  })),
);
const departments = useQuery(usersDepartmentListOptions());
const categories = useQuery(budgetsCategoryListOptions());

const createForm = ref({ departmentId: '', category: '', amount: '' });
const createError = ref('');
const createdNote = ref('');
const create = useMutation(budgetsLineCreateMutation());

async function submitCreate(): Promise<void> {
  createError.value = '';
  createdNote.value = '';
  try {
    const line = await create.mutateAsync({
      body: {
        department_id: createForm.value.departmentId,
        year_month: month.value,
        category: createForm.value.category,
        amount: createForm.value.amount,
      },
    });
    createdNote.value = `Created the ${line.category} budget for ${line.department_name}.`;
    createForm.value.amount = '';
    await lines.refetch();
  } catch (error) {
    createError.value = [problemMessage(error), ...problemFieldMessages(error)].join(' ');
  }
}

const editingId = ref<string | null>(null);
const replaceForm = ref({ amount: '', reason: '' });
const replaceError = ref('');
const replace = useMutation(budgetsLineReplaceMutation());

function startReplace(line: LineOut): void {
  editingId.value = line.line_id;
  replaceForm.value = { amount: line.amount, reason: '' };
  replaceError.value = '';
}

async function submitReplace(line: LineOut): Promise<void> {
  replaceError.value = '';
  try {
    await replace.mutateAsync({
      path: { line_id: line.line_id },
      body: {
        version: line.version,
        amount: replaceForm.value.amount,
        reason: replaceForm.value.reason,
      },
    });
    editingId.value = null;
    await lines.refetch();
  } catch (error) {
    // A 409 means someone else replaced this line first; the refetch shows the current version.
    replaceError.value = [problemMessage(error), ...problemFieldMessages(error)].join(' ');
    if (typeof error === 'object' && error !== null && 'status' in error && error.status === 409) {
      await lines.refetch();
    }
  }
}

const rows = computed(() => lines.data.value ?? []);
</script>

<template>
  <PageHeader
    title="Budgets"
    description="Budget per department, month and category. Replacing a line keeps its earlier versions."
  >
    <template #actions>
      <label
        :for="monthId"
        class="text-sm font-medium"
      >Month</label>
      <input
        :id="monthId"
        v-model="month"
        type="month"
        class="rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm"
      >
    </template>
  </PageHeader>

  <div class="grid gap-6">
    <SectionCard
      title="Budget lines"
      description="Only current versions are listed. Managers see their own department."
      flush
    >
      <p
        v-if="lines.isError.value"
        role="alert"
        class="m-5 rounded-lg border border-danger-line bg-danger-soft p-3 text-sm text-danger-fg"
      >
        {{ problemMessage(lines.error.value) }}
      </p>
      <p
        v-else-if="lines.isPending.value && MONTH.test(month)"
        class="p-5 text-sm text-muted"
      >
        Loading budget lines.
      </p>
      <p
        v-else-if="rows.length === 0"
        class="p-5 text-sm text-muted"
      >
        No budget lines for this month yet.
      </p>
      <div
        v-else
        class="overflow-x-auto"
      >
        <table class="w-full text-left text-sm">
          <caption class="sr-only">
            Budget lines for {{ month }}
          </caption>
          <thead class="bg-surface-muted text-muted">
            <tr>
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
                Amount (USD)
              </th>
              <th
                scope="col"
                class="px-5 py-2 font-medium"
              >
                Version and reason
              </th>
              <th
                scope="col"
                class="px-5 py-2 font-medium"
              >
                <span class="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            <template
              v-for="line in rows"
              :key="line.line_id"
            >
              <tr class="border-t border-line">
                <td class="px-5 py-3">
                  {{ line.department_name }}
                </td>
                <td class="px-5 py-3 capitalize">
                  {{ line.category }}
                </td>
                <td class="px-5 py-3 text-right tabular-nums">
                  {{ line.amount }}
                </td>
                <td class="px-5 py-3 text-muted">
                  v{{ line.version }}<span v-if="line.reason"> · {{ line.reason }}</span>
                </td>
                <td class="px-5 py-3 text-right">
                  <button
                    v-if="canManage && editingId !== line.line_id"
                    type="button"
                    class="rounded-lg border border-line px-3 py-1.5 text-sm font-medium"
                    @click="startReplace(line)"
                  >
                    Replace<span class="sr-only"> {{ line.department_name }} {{ line.category }}</span>
                  </button>
                </td>
              </tr>
              <tr
                v-if="canManage && editingId === line.line_id"
                class="border-t border-line bg-surface-muted"
              >
                <td
                  colspan="5"
                  class="px-5 py-4"
                >
                  <form
                    class="grid gap-3 sm:grid-cols-[10rem_1fr_auto] sm:items-end"
                    @submit.prevent="submitReplace(line)"
                  >
                    <label class="grid gap-1 text-sm font-medium">
                      New amount (USD)
                      <input
                        v-model="replaceForm.amount"
                        :class="fieldClass"
                        inputmode="decimal"
                        required
                      >
                    </label>
                    <label class="grid gap-1 text-sm font-medium">
                      Reason for the change
                      <input
                        v-model="replaceForm.reason"
                        :class="fieldClass"
                        maxlength="500"
                        required
                      >
                    </label>
                    <div class="flex gap-2">
                      <button
                        type="submit"
                        :class="buttonClass"
                        :disabled="replace.isPending.value"
                      >
                        Save version
                      </button>
                      <button
                        type="button"
                        class="rounded-lg border border-line px-3 py-2 text-sm font-medium"
                        @click="editingId = null"
                      >
                        Cancel
                      </button>
                    </div>
                    <p
                      v-if="replaceError"
                      role="alert"
                      class="text-sm text-danger-fg sm:col-span-3"
                    >
                      {{ replaceError }}
                    </p>
                  </form>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </SectionCard>

    <SectionCard
      v-if="canManage"
      title="Add a budget line"
      description="One line per department, month and category. A second current line for the same key is refused."
    >
      <form
        class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4 lg:items-end"
        @submit.prevent="submitCreate"
      >
        <label class="grid gap-1 text-sm font-medium">
          Department
          <select
            v-model="createForm.departmentId"
            :class="fieldClass"
            required
          >
            <option
              value=""
              disabled
            >
              Choose a department
            </option>
            <option
              v-for="department in departments.data.value ?? []"
              :key="department.id"
              :value="department.id"
            >
              {{ department.name }}
            </option>
          </select>
        </label>
        <label class="grid gap-1 text-sm font-medium">
          Category
          <select
            v-model="createForm.category"
            :class="fieldClass"
            required
          >
            <option
              value=""
              disabled
            >
              Choose a category
            </option>
            <option
              v-for="category in categories.data.value ?? []"
              :key="category"
              :value="category"
            >
              {{ category }}
            </option>
          </select>
        </label>
        <label class="grid gap-1 text-sm font-medium">
          Amount (USD)
          <input
            v-model="createForm.amount"
            :class="fieldClass"
            inputmode="decimal"
            placeholder="1200.00"
            required
          >
        </label>
        <button
          type="submit"
          :class="buttonClass"
          :disabled="create.isPending.value || !MONTH.test(month)"
        >
          Add line for {{ month }}
        </button>
      </form>
      <p
        v-if="createError"
        role="alert"
        class="mt-4 text-sm text-danger-fg"
      >
        {{ createError }}
      </p>
      <p
        v-if="createdNote"
        role="status"
        class="mt-4 text-sm text-success-fg"
      >
        {{ createdNote }}
      </p>
    </SectionCard>
  </div>
</template>
