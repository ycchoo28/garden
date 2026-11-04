<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query';
import { computed, ref } from 'vue';

import {
  knowledgeBatchPrepareMutation,
  knowledgeBatchPreviewMutation,
  knowledgeBatchReadOptions,
  knowledgeBatchReadQueryKey,
  knowledgeBatchUploadMutation,
} from '../../api/generated/@tanstack/vue-query.gen';
import { problemMessage } from '../../shared/problem';
import PageHeader from '../../shared/ui/PageHeader.vue';
import SectionCard from '../../shared/ui/SectionCard.vue';

const props = defineProps<{ batchId: string }>();
const queryClient = useQueryClient();

// While the background job runs the batch is `preparing`, so the page checks again every 2 seconds.
const batchQuery = useQuery({
  ...knowledgeBatchReadOptions({ path: { batch_id: props.batchId } }),
  refetchInterval: (query) => (query.state.data?.state === 'preparing' ? 2000 : false),
});
const batch = computed(() => batchQuery.data.value);
const isUploading = computed(() => batch.value?.state === 'uploaded');
const actionError = ref<string | null>(null);

const refreshBatch = () =>
  queryClient.invalidateQueries({ queryKey: knowledgeBatchReadQueryKey({ path: { batch_id: props.batchId } }) });

const file = ref<File | null>(null);
const translationGroup = ref('');

const upload = useMutation({
  ...knowledgeBatchUploadMutation(),
  onSuccess: async () => {
    file.value = null;
    translationGroup.value = '';
    actionError.value = null;
    await refreshBatch();
  },
  onError: (failure) => {
    actionError.value = problemMessage(failure);
  },
});

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  file.value = input.files?.[0] ?? null;
}

function submitFile() {
  if (!file.value) return;
  actionError.value = null;
  upload.mutate({
    path: { batch_id: props.batchId },
    body: { file: file.value, translation_group: translationGroup.value.trim() || null },
  });
}

const prepare = useMutation({
  ...knowledgeBatchPrepareMutation(),
  onSuccess: refreshBatch,
  onError: (failure) => {
    actionError.value = problemMessage(failure);
  },
});

function startPreparing() {
  actionError.value = null;
  prepare.mutate({ path: { batch_id: props.batchId } });
}

const query = ref('');
const preview = useMutation({
  ...knowledgeBatchPreviewMutation(),
  onError: (failure) => {
    actionError.value = problemMessage(failure);
  },
});

function runPreview() {
  actionError.value = null;
  preview.mutate({ path: { batch_id: props.batchId }, body: { query: query.value.trim(), limit: 5 } });
}
</script>

<template>
  <PageHeader
    title="Import batch"
    :description="batch ? `State: ${batch.state.replace('_', ' ')}` : 'Loading the batch.'"
  />

  <p
    v-if="batchQuery.error.value"
    class="text-sm text-muted"
    role="alert"
  >
    {{ problemMessage(batchQuery.error.value) }}
  </p>

  <div
    v-else-if="batch"
    class="grid gap-6 lg:grid-cols-2"
  >
    <SectionCard
      title="Files"
      :description="`${batch.files.length} file(s) in this batch. Markdown, text and PDF are accepted.`"
      flush
    >
      <ul
        v-if="batch.files.length"
        class="divide-y divide-line"
      >
        <li
          v-for="row in batch.files"
          :key="row.id"
          class="px-5 py-3"
        >
          <p class="font-medium">
            {{ row.name }}
          </p>
          <p class="text-sm text-muted">
            {{ row.translation_group }}
            <span v-if="row.parse_status"> · {{ row.parse_status }}</span>
            <span v-if="row.parse_error"> · {{ row.parse_error }}</span>
          </p>
        </li>
      </ul>
      <p
        v-else
        class="px-5 py-4 text-sm text-muted"
      >
        No files yet.
      </p>
    </SectionCard>

    <SectionCard
      title="Batch actions"
      description="Add files, then prepare the batch. Preparation runs in the background."
    >
      <form
        v-if="isUploading"
        class="space-y-4"
        @submit.prevent="submitFile"
      >
        <label class="block space-y-1">
          <span class="text-sm font-medium">File</span>
          <input
            type="file"
            name="file"
            accept=".md,.markdown,.txt,.pdf"
            required
            @change="onFileChange"
          >
        </label>
        <label class="block space-y-1">
          <span class="text-sm font-medium">Translation group (optional)</span>
          <input
            v-model="translationGroup"
            name="translation_group"
            maxlength="200"
            class="w-full rounded-lg border border-line bg-surface px-3 py-2"
          >
        </label>
        <button
          type="submit"
          class="rounded-lg border border-line px-4 py-2 text-sm font-medium"
          :disabled="!file || upload.isPending.value"
        >
          Add file
        </button>
      </form>
      <button
        v-if="isUploading"
        type="button"
        class="mt-4 rounded-lg bg-primary px-4 py-2 font-semibold text-primary-contrast"
        :disabled="batch.files.length === 0 || prepare.isPending.value"
        @click="startPreparing"
      >
        Prepare batch
      </button>
      <p
        v-if="batch.state === 'preparing'"
        class="mt-4 text-sm text-muted"
      >
        Preparing: parsing, chunking and embedding the files.
      </p>
      <div
        v-if="batch.state === 'failed'"
        class="mt-4 text-sm"
        role="alert"
      >
        <p class="font-medium">
          Preparation failed.
        </p>
        <p class="text-muted">
          {{ batch.failure_reason }}
        </p>
      </div>
      <p
        v-if="actionError"
        class="mt-4 text-sm text-muted"
        role="alert"
      >
        {{ actionError }}
      </p>
    </SectionCard>

    <SectionCard
      v-if="batch.state === 'prepared'"
      title="Preview search"
      description="Search the prepared batch the way a reader would. Results follow your access rules."
      class="lg:col-span-2"
    >
      <form
        class="flex flex-wrap gap-3"
        @submit.prevent="runPreview"
      >
        <input
          v-model="query"
          name="query"
          required
          maxlength="500"
          aria-label="Preview query"
          class="min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 py-2"
        >
        <button
          type="submit"
          class="rounded-lg bg-primary px-4 py-2 font-semibold text-primary-contrast"
          :disabled="preview.isPending.value"
        >
          Search
        </button>
      </form>
      <div
        v-if="preview.data.value"
        class="mt-6 space-y-4"
      >
        <p
          v-if="preview.data.value.truncated"
          class="text-sm text-muted"
        >
          Some results were left out to stay within the token budget.
        </p>
        <p
          v-if="preview.data.value.results.length === 0"
          class="text-sm text-muted"
        >
          No results.
        </p>
        <article
          v-for="hit in preview.data.value.results"
          :key="hit.chunk_ids.join(',')"
          class="rounded-lg border border-line p-4"
        >
          <p class="font-medium">
            {{ hit.title }}
          </p>
          <p class="text-sm text-muted">
            {{ hit.heading_path }}
            <span v-if="hit.locator"> · {{ hit.locator }}</span>
            · {{ hit.language }} · {{ hit.token_count }} tokens
          </p>
          <p class="mt-2 whitespace-pre-line text-sm">
            {{ hit.text }}
          </p>
        </article>
      </div>
    </SectionCard>
  </div>
</template>
