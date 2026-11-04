<script setup lang="ts">
import { useMutation, useQuery } from '@tanstack/vue-query';
import { computed, ref, useId } from 'vue';

import {
  spendMonthCloseMutation,
  spendMonthListOptions,
  spendStatementImportMutation,
  spendStatementListOptions,
  spendStatementPreviewMutation,
  spendStatementVoidMutation,
  usersSelfReadOptions,
} from '../../api/generated/@tanstack/vue-query.gen';
import type { StatementOut, StatementPreviewOut } from '../../api/generated/types.gen';
import { problemFieldMessages, problemMessage } from '../../shared/problem';
import PageHeader from '../../shared/ui/PageHeader.vue';
import SectionCard from '../../shared/ui/SectionCard.vue';

const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;
const fieldClass =
  'w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm disabled:opacity-60';
const buttonClass =
  'rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-contrast hover:bg-primary-hover disabled:opacity-60';
const secondaryButtonClass = 'rounded-lg border border-line px-3 py-2 text-sm font-medium';

const { data: me } = useQuery(usersSelfReadOptions());
const permissions = computed(() => me.value?.permissions ?? []);
const canImport = computed(() => permissions.value.includes('spend.statement.import'));
const canVoid = computed(() => permissions.value.includes('spend.statement.void'));
const canClose = computed(() => permissions.value.includes('spend.month.close'));
const canView = computed(() => permissions.value.includes('spend.statement.view'));

const fileId = useId();
const file = ref<File | null>(null);
const preview = ref<StatementPreviewOut | null>(null);
const checkError = ref('');
const applyError = ref('');
const appliedNote = ref('');

const check = useMutation(spendStatementPreviewMutation());
const apply = useMutation(spendStatementImportMutation());
const statements = useQuery({ ...spendStatementListOptions(), enabled: canView });
const months = useQuery({ ...spendMonthListOptions(), enabled: canView });

function onFileChange(event: Event): void {
  const input = event.target as HTMLInputElement;
  file.value = input.files?.[0] ?? null;
  // A new file invalidates the earlier dry run, so the apply button waits for a fresh check.
  preview.value = null;
  checkError.value = '';
  applyError.value = '';
  appliedNote.value = '';
}

async function runCheck(): Promise<void> {
  if (!file.value) return;
  checkError.value = '';
  appliedNote.value = '';
  try {
    preview.value = await check.mutateAsync({ body: { file: file.value } });
  } catch (error) {
    preview.value = null;
    checkError.value = [problemMessage(error), ...problemFieldMessages(error)].join(' ');
  }
}

async function runApply(): Promise<void> {
  if (!file.value || !preview.value?.can_apply) return;
  applyError.value = '';
  try {
    const statement = await apply.mutateAsync({ body: { file: file.value } });
    appliedNote.value = `Saved ${statement.source_name}: ${statement.row_count} rows totalling ${statement.total_amount} USD.`;
    file.value = null;
    preview.value = null;
    await statements.refetch();
  } catch (error) {
    applyError.value = [problemMessage(error), ...problemFieldMessages(error)].join(' ');
    await statements.refetch();
  }
}

const voidTargetId = ref<string | null>(null);
const voidReason = ref('');
const voidError = ref('');
const voidMutation = useMutation(spendStatementVoidMutation());

function startVoid(statement: StatementOut): void {
  voidTargetId.value = statement.id;
  voidReason.value = '';
  voidError.value = '';
}

async function runVoid(statement: StatementOut): Promise<void> {
  voidError.value = '';
  try {
    await voidMutation.mutateAsync({
      path: { statement_id: statement.id },
      body: { reason: voidReason.value },
    });
    voidTargetId.value = null;
    voidReason.value = '';
    await statements.refetch();
  } catch (error) {
    voidError.value = [problemMessage(error), ...problemFieldMessages(error)].join(' ');
  }
}

const closeMonthId = useId();
const closeMonth = ref('');
const closeReason = ref('');
const closeError = ref('');
const closeNote = ref('');
const close = useMutation(spendMonthCloseMutation());

async function runClose(): Promise<void> {
  closeError.value = '';
  closeNote.value = '';
  try {
    const closed = await close.mutateAsync({
      path: { year_month: closeMonth.value },
      body: { reason: closeReason.value },
    });
    closeNote.value = `Closed ${closed.year_month}. No spend for it can change now.`;
    closeMonth.value = '';
    closeReason.value = '';
    await months.refetch();
  } catch (error) {
    closeError.value = [problemMessage(error), ...problemFieldMessages(error)].join(' ');
  }
}

const closeReady = computed(() => MONTH.test(closeMonth.value) && closeReason.value.trim() !== '');
const closedMonths = computed(() => months.data.value ?? []);
const statementRows = computed(() => statements.data.value ?? []);

function shortTime(value: string): string {
  return value.replace('T', ' ').slice(0, 16);
}
</script>

<template>
  <PageHeader
    title="Card statements"
    description="Import a company card CSV. Check it first: nothing is saved until every row passes."
  />

  <div class="grid gap-6">
    <SectionCard
      v-if="canImport"
      title="Import a statement"
      description="Choose a CSV with the columns transaction_ref, posted_on, department_code, category, merchant and amount_usd."
    >
      <div class="grid gap-4">
        <div class="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
          <label
            :for="fileId"
            class="grid gap-1 text-sm font-medium"
          >
            Statement CSV
            <input
              :id="fileId"
              type="file"
              accept=".csv,text/csv"
              class="text-sm file:mr-3 file:rounded-lg file:border file:border-line file:bg-surface-muted file:px-3 file:py-2 file:text-sm file:font-medium"
              @change="onFileChange"
            >
          </label>
          <button
            type="button"
            :class="buttonClass"
            :disabled="!file || check.isPending.value"
            @click="runCheck"
          >
            Check statement
          </button>
        </div>

        <p
          v-if="checkError"
          role="alert"
          class="rounded-lg border border-danger-line bg-danger-soft p-3 text-sm text-danger-fg"
        >
          {{ checkError }}
        </p>

        <div
          v-if="preview"
          class="grid gap-4 rounded-lg border border-line p-4"
          aria-live="polite"
        >
          <dl class="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
            <div>
              <dt class="text-muted">
                Rows read
              </dt>
              <dd class="font-semibold tabular-nums">
                {{ preview.rows_read }}
              </dd>
            </div>
            <div>
              <dt class="text-muted">
                Total (USD)
              </dt>
              <dd class="font-semibold tabular-nums">
                {{ preview.total_amount }}
              </dd>
            </div>
            <div>
              <dt class="text-muted">
                Row errors
              </dt>
              <dd class="font-semibold tabular-nums">
                {{ preview.errors.length }}
              </dd>
            </div>
            <div>
              <dt class="text-muted">
                Duplicates
              </dt>
              <dd class="font-semibold tabular-nums">
                {{ preview.duplicates.length }}
              </dd>
            </div>
          </dl>

          <p
            v-if="preview.can_apply"
            class="rounded-lg border border-success-line bg-success-soft p-3 text-sm text-success-fg"
          >
            Every row passes. You can save the whole statement.
          </p>
          <p
            v-else
            class="rounded-lg border border-warn-line bg-warn-soft p-3 text-sm text-warn-fg"
          >
            Nothing will be saved until every row below is fixed. Upload a corrected file to check again.
          </p>

          <div
            v-if="preview.errors.length"
            class="overflow-x-auto"
          >
            <table class="w-full text-left text-sm">
              <caption class="mb-2 text-left font-semibold">
                Row errors
              </caption>
              <thead class="text-muted">
                <tr>
                  <th
                    scope="col"
                    class="py-1 pr-4 font-medium"
                  >
                    Row
                  </th>
                  <th
                    scope="col"
                    class="py-1 pr-4 font-medium"
                  >
                    Column
                  </th>
                  <th
                    scope="col"
                    class="py-1 font-medium"
                  >
                    Problem
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="issue in preview.errors"
                  :key="`${issue.row}-${issue.field}-${issue.message}`"
                  class="border-t border-line"
                >
                  <td class="py-2 pr-4 tabular-nums">
                    {{ issue.row }}
                  </td>
                  <td class="py-2 pr-4 font-mono">
                    {{ issue.field }}
                  </td>
                  <td class="py-2 text-danger-fg">
                    {{ issue.message }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <div
            v-if="preview.duplicates.length"
            class="overflow-x-auto"
          >
            <table class="w-full text-left text-sm">
              <caption class="mb-2 text-left font-semibold">
                Duplicate rows
              </caption>
              <thead class="text-muted">
                <tr>
                  <th
                    scope="col"
                    class="py-1 pr-4 font-medium"
                  >
                    Row
                  </th>
                  <th
                    scope="col"
                    class="py-1 pr-4 font-medium"
                  >
                    Transaction
                  </th>
                  <th
                    scope="col"
                    class="py-1 font-medium"
                  >
                    Why
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="issue in preview.duplicates"
                  :key="`${issue.row}-${issue.message}`"
                  class="border-t border-line"
                >
                  <td class="py-2 pr-4 tabular-nums">
                    {{ issue.row }}
                  </td>
                  <td class="py-2 pr-4 font-mono">
                    {{ issue.transaction_ref }}
                  </td>
                  <td class="py-2 text-warn-fg">
                    {{ issue.message }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="flex flex-wrap items-center gap-3">
            <button
              type="button"
              :class="buttonClass"
              :disabled="!preview.can_apply || apply.isPending.value"
              @click="runApply"
            >
              Save all rows
            </button>
            <span
              v-if="!preview.can_apply"
              class="text-sm text-muted"
            >
              Disabled until the statement has no errors or duplicates.
            </span>
          </div>
        </div>

        <p
          v-if="applyError"
          role="alert"
          class="rounded-lg border border-danger-line bg-danger-soft p-3 text-sm text-danger-fg"
        >
          {{ applyError }}
        </p>
        <p
          v-if="appliedNote"
          role="status"
          class="rounded-lg border border-success-line bg-success-soft p-3 text-sm text-success-fg"
        >
          {{ appliedNote }}
        </p>
      </div>
    </SectionCard>

    <SectionCard
      v-if="canView"
      title="Imported statements"
      description="A voided statement stops counting as spend. Its rows stay on record with who voided it and why."
      flush
    >
      <p
        v-if="statementRows.length === 0"
        class="p-5 text-sm text-muted"
      >
        No statements imported yet.
      </p>
      <div
        v-else
        class="overflow-x-auto"
      >
        <table class="w-full text-left text-sm">
          <caption class="sr-only">
            Imported card statements
          </caption>
          <thead class="bg-surface-muted text-muted">
            <tr>
              <th
                scope="col"
                class="px-5 py-2 font-medium"
              >
                Statement
              </th>
              <th
                scope="col"
                class="px-5 py-2 font-medium"
              >
                Imported
              </th>
              <th
                scope="col"
                class="px-5 py-2 text-right font-medium"
              >
                Rows
              </th>
              <th
                scope="col"
                class="px-5 py-2 text-right font-medium"
              >
                Total (USD)
              </th>
              <th
                scope="col"
                class="px-5 py-2 font-medium"
              >
                Status
              </th>
            </tr>
          </thead>
          <tbody>
            <template
              v-for="statement in statementRows"
              :key="statement.id"
            >
              <tr class="border-t border-line">
                <td class="px-5 py-3">
                  {{ statement.source_name }}
                </td>
                <td class="px-5 py-3 text-muted tabular-nums">
                  {{ shortTime(statement.imported_at) }}
                </td>
                <td class="px-5 py-3 text-right tabular-nums">
                  {{ statement.row_count }}
                </td>
                <td class="px-5 py-3 text-right tabular-nums">
                  {{ statement.total_amount }}
                </td>
                <td class="px-5 py-3">
                  <span
                    v-if="statement.voided"
                    class="text-muted"
                  >
                    Voided {{ shortTime(statement.voided_at ?? '') }}: {{ statement.void_reason }}
                  </span>
                  <span
                    v-else-if="canVoid && voidTargetId !== statement.id"
                  >
                    <button
                      type="button"
                      :class="secondaryButtonClass"
                      @click="startVoid(statement)"
                    >
                      Void<span class="sr-only"> {{ statement.source_name }}</span>
                    </button>
                  </span>
                  <span
                    v-else
                    class="text-success-fg"
                  >Active</span>
                </td>
              </tr>
              <tr
                v-if="canVoid && voidTargetId === statement.id"
                class="border-t border-line bg-surface-muted"
              >
                <td
                  colspan="5"
                  class="px-5 py-4"
                >
                  <form
                    class="grid gap-3 sm:grid-cols-[1fr_auto_auto] sm:items-end"
                    @submit.prevent="runVoid(statement)"
                  >
                    <label class="grid gap-1 text-sm font-medium">
                      Why void {{ statement.source_name }}?
                      <input
                        v-model="voidReason"
                        :class="fieldClass"
                        maxlength="500"
                        required
                      >
                    </label>
                    <button
                      type="submit"
                      :class="buttonClass"
                      :disabled="voidMutation.isPending.value || voidReason.trim() === ''"
                    >
                      Void statement
                    </button>
                    <button
                      type="button"
                      :class="secondaryButtonClass"
                      @click="voidTargetId = null"
                    >
                      Cancel
                    </button>
                    <p
                      v-if="voidError"
                      role="alert"
                      class="text-sm text-danger-fg sm:col-span-3"
                    >
                      {{ voidError }}
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
      v-if="canView"
      title="Closed months"
      description="After a month is closed, no spend for it can be imported or voided."
    >
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
        v-if="canClose"
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
    </SectionCard>
  </div>
</template>
