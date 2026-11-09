<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query';
import { computed, ref, watch } from 'vue';

import type { ClaimDetailOut } from '../../../api/generated/types.gen';
import {
  budgetsCategoryListOptions,
  claimsClaimDecideMutation,
  claimsClaimListOptions,
  claimsClaimReadOptions,
  claimsClaimSubmitMutation,
  claimsClaimWithdrawMutation,
  claimsLineAddMutation,
  filesFileUploadMutation,
} from '../../../api/generated/@tanstack/vue-query.gen';
import { problemMessage } from '../../../shared/problem';
import { PageHeader, SectionCard } from '../../../shared/ui';
import ClaimStatus from '../ClaimStatus.vue';
import {
  categoryLabel,
  formatAmount,
  formatDateTime,
  roleLabel,
  runStateLabel,
} from '../format';

const props = defineProps<{ id: string }>();

const queryClient = useQueryClient();
const readOptions = () => claimsClaimReadOptions({ path: { claim_id: props.id } });
const { data: detail, isPending, isError, error } = useQuery(() => readOptions());
const categoriesQuery = useQuery(() => budgetsCategoryListOptions());

// Every change returns the claim as it now stands, so the page shows the new version at once.
const actionError = ref('');

function onChanged(next: ClaimDetailOut): void {
  actionError.value = '';
  queryClient.setQueryData(readOptions().queryKey, next);
  void queryClient.invalidateQueries({ queryKey: claimsClaimListOptions().queryKey });
}

// A refused change, such as a stale version (409), shows its reason and reloads the claim.
function onRefused(failure: unknown): void {
  actionError.value = problemMessage(failure);
  void queryClient.invalidateQueries({ queryKey: readOptions().queryKey });
}

const upload = useMutation(filesFileUploadMutation());
const addLine = useMutation({ ...claimsLineAddMutation(), onSuccess: onChanged });
const submit = useMutation({ ...claimsClaimSubmitMutation(), onSuccess: onChanged, onError: onRefused });
const withdraw = useMutation({ ...claimsClaimWithdrawMutation(), onSuccess: onChanged, onError: onRefused });
const decide = useMutation({ ...claimsClaimDecideMutation(), onSuccess: onChanged, onError: onRefused });

const busy = computed(
  () =>
    upload.isPending.value ||
    addLine.isPending.value ||
    submit.isPending.value ||
    withdraw.isPending.value ||
    decide.isPending.value,
);

// The new line form. It is keyed so that a saved line clears every field.
const formKey = ref(0);
const category = ref('');
watch(categoriesQuery.data, (categories) => {
  const first = categories?.[0];
  if (first && !categories.includes(category.value)) category.value = first;
}, { immediate: true });
const spentOn = ref('');
const amount = ref('');
const description = ref('');
const receipt = ref<File | null>(null);
const lineReady = computed(
  () =>
    receipt.value !== null &&
    categoriesQuery.isSuccess.value &&
    categoriesQuery.data.value?.includes(category.value) === true &&
    spentOn.value !== '' &&
    amount.value !== '' &&
    Number(amount.value) > 0 &&
    description.value.trim() !== '' &&
    !busy.value,
);

function onReceipt(event: Event): void {
  const input = event.target as HTMLInputElement;
  receipt.value = input.files?.[0] ?? null;
}

async function saveLine(): Promise<void> {
  if (!detail.value || !receipt.value || !lineReady.value) return;
  try {
    const uploaded = await upload.mutateAsync({ body: { file: receipt.value } });
    const saved = await addLine.mutateAsync({
      path: { claim_id: props.id },
      body: {
        expected_version: detail.value.claim.version,
        category: category.value,
        spent_on: spentOn.value,
        amount: amount.value,
        description: description.value.trim(),
        receipt_sha256: uploaded.sha256,
      },
    });
    onChanged(saved);
    formKey.value += 1;
    receipt.value = null;
    spentOn.value = '';
    amount.value = '';
    description.value = '';
  } catch (failure) {
    onRefused(failure);
  }
}

// Certifying is a person's own act, so the submit button waits for the tick box.
const certified = ref(false);

function submitClaim(): void {
  if (!detail.value || !certified.value) return;
  submit.mutate({ path: { claim_id: props.id }, body: { expected_version: detail.value.claim.version } });
}

function withdrawClaim(): void {
  if (!detail.value) return;
  withdraw.mutate({ path: { claim_id: props.id }, body: { expected_version: detail.value.claim.version } });
}

const reason = ref('');

function decideClaim(decision: 'approve' | 'reject'): void {
  if (!detail.value) return;
  decide.mutate(
    {
      path: { claim_id: props.id },
      body: {
        expected_version: detail.value.claim.version,
        decision,
        reason: reason.value.trim() || null,
      },
    },
    { onSuccess: () => (reason.value = '') },
  );
}

const canReject = computed(() => reason.value.trim() !== '' && !busy.value);
</script>

<template>
  <p
    v-if="isPending"
    class="text-muted"
  >
    Loading the claim.
  </p>
  <p
    v-else-if="isError"
    role="alert"
    class="text-danger-fg"
  >
    {{ problemMessage(error) }}
  </p>
  <template v-else-if="detail">
    <PageHeader
      :title="detail.claim.title"
      :description="`${detail.claim.claimant_name} · ${detail.claim.department_code} · Total ${formatAmount(detail.claim.total)}`"
    >
      <template #actions>
        <ClaimStatus :status="detail.claim.status" />
      </template>
    </PageHeader>

    <p
      v-if="actionError"
      role="alert"
      class="mb-6 rounded-lg border border-danger-line bg-danger-soft px-4 py-3 text-danger-fg"
    >
      {{ actionError }}
    </p>

    <div class="grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
      <div class="min-w-0 space-y-6">
        <SectionCard
          title="Lines"
          description="Each line needs a receipt. A rejected claim can be corrected by adding or voiding lines."
          flush
        >
          <p
            v-if="detail.lines.length === 0"
            class="p-5 text-muted"
          >
            No lines yet.
          </p>
          <div
            v-else
            class="overflow-x-auto"
          >
            <table class="w-full text-left text-sm">
              <caption class="sr-only">
                Lines on this claim
              </caption>
              <thead class="border-b border-line text-muted">
                <tr>
                  <th
                    scope="col"
                    class="px-5 py-3 font-medium"
                  >
                    Date
                  </th>
                  <th
                    scope="col"
                    class="px-3 py-3 font-medium"
                  >
                    Category
                  </th>
                  <th
                    scope="col"
                    class="px-3 py-3 font-medium"
                  >
                    Description
                  </th>
                  <th
                    scope="col"
                    class="px-3 py-3 text-right font-medium"
                  >
                    Amount
                  </th>
                  <th
                    scope="col"
                    class="px-5 py-3 font-medium"
                  >
                    Receipt
                  </th>
                </tr>
              </thead>
              <tbody class="divide-y divide-line">
                <tr
                  v-for="line in detail.lines"
                  :key="line.id"
                >
                  <td class="whitespace-nowrap px-5 py-3">
                    {{ line.spent_on }}
                  </td>
                  <td class="px-3 py-3">
                    {{ categoryLabel(line.category) }}
                  </td>
                  <td class="px-3 py-3">
                    {{ line.description }}
                  </td>
                  <td class="px-3 py-3 text-right tabular-nums">
                    {{ formatAmount(line.amount) }}
                  </td>
                  <td class="px-5 py-3">
                    <a
                      :href="line.receipt_url"
                      target="_blank"
                      rel="noopener"
                      class="font-medium text-primary underline underline-offset-2"
                    >View receipt<span class="sr-only"> for {{ line.description }}</span></a>
                  </td>
                </tr>
              </tbody>
              <tfoot class="border-t border-line font-semibold">
                <tr>
                  <th
                    scope="row"
                    colspan="3"
                    class="px-5 py-3 text-left"
                  >
                    Total
                  </th>
                  <td class="px-3 py-3 text-right tabular-nums">
                    {{ formatAmount(detail.claim.total) }}
                  </td>
                  <td />
                </tr>
              </tfoot>
            </table>
          </div>
        </SectionCard>

        <SectionCard
          v-if="detail.actions.can_edit"
          title="Add a line"
          description="Attach the receipt, then save the line. The receipt is stored with the line."
        >
          <form
            :key="formKey"
            class="grid gap-4 sm:grid-cols-2"
            @submit.prevent="saveLine"
          >
            <label class="block">
              <span class="mb-1 block text-sm font-medium">Category</span>
              <select
                v-model="category"
                :disabled="categoriesQuery.isPending.value || categoriesQuery.isError.value || !categoriesQuery.data.value?.length"
                class="w-full rounded-lg border border-line-strong bg-surface px-3 py-2"
              >
                <option
                  v-for="option in categoriesQuery.data.value ?? []"
                  :key="option"
                  :value="option"
                >
                  {{ categoryLabel(option) }}
                </option>
              </select>
              <span
                v-if="categoriesQuery.isPending.value"
                class="mt-1 block text-sm text-muted"
                role="status"
              >Loading categories…</span>
              <span
                v-else-if="categoriesQuery.isError.value"
                class="mt-1 block text-sm text-danger"
                role="alert"
              >{{ problemMessage(categoriesQuery.error.value) }}</span>
              <span
                v-else-if="!categoriesQuery.data.value?.length"
                class="mt-1 block text-sm text-danger"
                role="alert"
              >No categories are available.</span>
            </label>
            <label class="block">
              <span class="mb-1 block text-sm font-medium">Date spent</span>
              <input
                v-model="spentOn"
                type="date"
                required
                class="w-full rounded-lg border border-line-strong bg-surface px-3 py-2"
              >
            </label>
            <label class="block">
              <span class="mb-1 block text-sm font-medium">Amount</span>
              <input
                v-model="amount"
                type="number"
                min="0.01"
                step="0.01"
                required
                class="w-full rounded-lg border border-line-strong bg-surface px-3 py-2 tabular-nums"
              >
            </label>
            <label class="block">
              <span class="mb-1 block text-sm font-medium">Description</span>
              <input
                v-model="description"
                type="text"
                maxlength="300"
                required
                class="w-full rounded-lg border border-line-strong bg-surface px-3 py-2"
              >
            </label>
            <label class="block sm:col-span-2">
              <span class="mb-1 block text-sm font-medium">Receipt</span>
              <input
                type="file"
                accept="image/png,image/jpeg,application/pdf"
                required
                class="w-full text-sm"
                @change="onReceipt"
              >
            </label>
            <div class="sm:col-span-2">
              <button
                type="submit"
                class="rounded-lg bg-primary px-4 py-2 font-medium text-primary-contrast hover:bg-primary-hover disabled:opacity-60"
                :disabled="!lineReady"
              >
                Save line
              </button>
            </div>
          </form>
        </SectionCard>
      </div>

      <div class="min-w-0 space-y-6">
        <SectionCard
          v-if="detail.actions.can_submit || detail.actions.can_withdraw"
          title="Submit"
        >
          <form
            v-if="detail.actions.can_submit"
            class="space-y-4"
            @submit.prevent="submitClaim"
          >
            <label class="flex items-start gap-3">
              <input
                v-model="certified"
                type="checkbox"
                class="mt-1 size-4"
              >
              <span class="text-sm">I certify that these lines are true, and that the receipts are genuine.</span>
            </label>
            <button
              type="submit"
              class="w-full rounded-lg bg-primary px-4 py-2 font-medium text-primary-contrast hover:bg-primary-hover disabled:opacity-60"
              :disabled="!certified || busy"
            >
              Certify and submit
            </button>
          </form>
          <button
            v-if="detail.actions.can_withdraw"
            type="button"
            class="mt-4 w-full rounded-lg border border-line px-4 py-2 font-medium disabled:opacity-60"
            :disabled="busy"
            @click="withdrawClaim"
          >
            Withdraw claim
          </button>
        </SectionCard>

        <SectionCard
          v-if="detail.actions.can_decide"
          title="Decision"
          description="A rejection needs a reason. The claimant sees it, and can correct the claim and resubmit."
        >
          <div class="space-y-4">
            <label class="block">
              <span class="mb-1 block text-sm font-medium">Reason</span>
              <textarea
                v-model="reason"
                rows="3"
                maxlength="1000"
                class="w-full rounded-lg border border-line-strong bg-surface px-3 py-2"
              />
            </label>
            <div class="flex flex-wrap gap-2">
              <button
                type="button"
                class="rounded-lg bg-primary px-4 py-2 font-medium text-primary-contrast hover:bg-primary-hover disabled:opacity-60"
                :disabled="busy"
                @click="decideClaim('approve')"
              >
                Approve
              </button>
              <button
                type="button"
                class="rounded-lg border border-danger-line bg-danger-soft px-4 py-2 font-medium text-danger-fg disabled:opacity-60"
                :disabled="!canReject"
                @click="decideClaim('reject')"
              >
                Reject
              </button>
            </div>
          </div>
        </SectionCard>

        <SectionCard
          title="History"
          description="Every decision on this claim, newest first. Earlier decisions stay on the record."
        >
          <ol class="space-y-5">
            <li
              v-for="(run, index) in detail.history"
              :key="run.id"
            >
              <p class="font-medium">
                {{ index === 0 ? 'Current request' : 'Earlier request' }}:
                {{ runStateLabel(run.state) }}
              </p>
              <p class="text-sm text-muted">
                Sent by {{ run.started_by_name }} on {{ formatDateTime(run.started_at) }}
              </p>
              <ul class="mt-2 space-y-2 border-l border-line pl-4">
                <li
                  v-for="step in run.steps"
                  :key="step.position"
                  class="text-sm"
                >
                  <span class="font-medium">{{ roleLabel(step.role) }}</span>
                  <template v-if="step.decision">
                    {{ step.decision.outcome === 'approved' ? 'approved' : 'rejected' }}
                    by {{ step.decision.decided_by_name }}
                    on {{ formatDateTime(step.decision.decided_at) }}
                    <p
                      v-if="step.decision.reason"
                      class="mt-1 text-muted"
                    >
                      "{{ step.decision.reason }}"
                    </p>
                  </template>
                  <span
                    v-else
                    class="text-muted"
                  >
                    {{ run.cancelled_at ? 'Not decided' : 'Waiting' }}
                  </span>
                </li>
              </ul>
              <p
                v-if="run.cancelled_at && run.cancel_reason"
                class="mt-2 text-sm text-muted"
              >
                {{ run.cancel_reason }}
              </p>
            </li>
          </ol>
        </SectionCard>
      </div>
    </div>
  </template>
</template>
