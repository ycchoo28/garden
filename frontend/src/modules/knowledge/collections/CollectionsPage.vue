<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query';
import { computed, ref } from 'vue';
import { useRouter } from 'vue-router';

import {
  knowledgeBatchCreateMutation,
  knowledgeCollectionCreateMutation,
  knowledgeCollectionListOptions,
  knowledgeCollectionListQueryKey,
  usersSelfReadOptions,
} from '../../../api/generated/@tanstack/vue-query.gen';
import { problemMessage } from '../../../shared/problem';
import { PageHeader, SectionCard } from '../../../shared/ui';

const router = useRouter();
const queryClient = useQueryClient();

const { data: me } = useQuery(usersSelfReadOptions());
const canManage = computed(() => me.value?.permissions.includes('knowledge.collection.manage') ?? false);
const canPrepare = computed(() => me.value?.permissions.includes('knowledge.batch.prepare') ?? false);
const { data: collections, error, isPending } = useQuery(knowledgeCollectionListOptions());

const name = ref('');
const role = ref('manager');
const departments = ref('');
const formError = ref<string | null>(null);

const createCollection = useMutation({
  ...knowledgeCollectionCreateMutation(),
  onSuccess: async () => {
    name.value = '';
    departments.value = '';
    await queryClient.invalidateQueries({ queryKey: knowledgeCollectionListQueryKey() });
  },
  onError: (failure) => {
    formError.value = problemMessage(failure);
  },
});

function submitCollection() {
  formError.value = null;
  const codes = departments.value
    .split(',')
    .map((code) => code.trim())
    .filter(Boolean);
  const rule = codes.length > 0 ? { role: role.value, department_codes: codes } : { role: role.value, all_departments: true };
  createCollection.mutate({ body: { name: name.value.trim(), rules: [rule] } });
}

const openBatch = useMutation({
  ...knowledgeBatchCreateMutation(),
  onSuccess: (batch) => router.push({ name: 'batch', params: { batchId: batch.id } }),
  onError: (failure) => {
    formError.value = problemMessage(failure);
  },
});

function startBatch(collectionId: string) {
  formError.value = null;
  openBatch.mutate({ path: { collection_id: collectionId }, body: { mode: 'full_set' } });
}
</script>

<template>
  <PageHeader
    title="Knowledge collections"
    description="A collection is a set of documents and the roles and departments that may read it."
  />

  <div class="grid gap-6 lg:grid-cols-[2fr_1fr]">
    <SectionCard
      title="Collections"
      flush
    >
      <p
        v-if="isPending"
        class="px-5 py-4 text-sm text-muted"
      >
        Loading collections.
      </p>
      <p
        v-else-if="error"
        class="px-5 py-4 text-sm text-muted"
        role="alert"
      >
        {{ problemMessage(error) }}
      </p>
      <p
        v-else-if="!collections?.length"
        class="px-5 py-4 text-sm text-muted"
      >
        No collections yet.
      </p>
      <ul
        v-else
        class="divide-y divide-line"
      >
        <li
          v-for="collection in collections"
          :key="collection.id"
          class="flex flex-wrap items-center justify-between gap-3 px-5 py-4"
        >
          <div class="min-w-0">
            <p class="font-medium">
              {{ collection.name }}
            </p>
            <p class="text-sm text-muted">
              <span
                v-for="rule in collection.rules"
                :key="`${rule.role}-${rule.department_codes.join(',')}-${rule.all_departments}`"
                class="mr-3"
              >
                {{ rule.role }}:
                {{ rule.all_departments ? 'all departments' : rule.department_codes.join(', ') }}
              </span>
              <span v-if="collection.rules.length === 0">No access rules, so nobody can read it.</span>
            </p>
          </div>
          <button
            v-if="canPrepare"
            type="button"
            class="rounded-lg border border-line px-3 py-2 text-sm font-medium"
            :disabled="openBatch.isPending.value"
            @click="startBatch(collection.id)"
          >
            Start an import batch
          </button>
        </li>
      </ul>
    </SectionCard>

    <SectionCard
      v-if="canManage"
      title="New collection"
      description="Name the role and the departments that may read it. Leave departments empty for every department."
    >
      <form
        class="space-y-4"
        @submit.prevent="submitCollection"
      >
        <label class="block space-y-1">
          <span class="text-sm font-medium">Name</span>
          <input
            v-model="name"
            name="name"
            required
            maxlength="200"
            class="w-full rounded-lg border border-line bg-surface px-3 py-2"
          >
        </label>
        <label class="block space-y-1">
          <span class="text-sm font-medium">Role that may read it</span>
          <input
            v-model="role"
            name="role"
            required
            maxlength="50"
            class="w-full rounded-lg border border-line bg-surface px-3 py-2"
          >
        </label>
        <label class="block space-y-1">
          <span class="text-sm font-medium">Departments (codes, comma separated)</span>
          <input
            v-model="departments"
            name="departments"
            class="w-full rounded-lg border border-line bg-surface px-3 py-2"
          >
        </label>
        <p
          v-if="formError"
          class="text-sm text-muted"
          role="alert"
        >
          {{ formError }}
        </p>
        <button
          type="submit"
          class="rounded-lg bg-primary px-4 py-2 font-semibold text-primary-contrast"
          :disabled="createCollection.isPending.value"
        >
          Create collection
        </button>
      </form>
    </SectionCard>
  </div>
</template>
