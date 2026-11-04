<script setup lang="ts">
import { useMutation } from '@tanstack/vue-query';
import { ref } from 'vue';

import { knowledgeSearchRunMutation } from '../../api/generated/@tanstack/vue-query.gen';
import { problemMessage } from '../../shared/problem';
import PageHeader from '../../shared/ui/PageHeader.vue';
import SectionCard from '../../shared/ui/SectionCard.vue';

const query = ref('');
// An empty moment searches what is live now. A chosen moment searches what was live then.
const asOf = ref('');
const searchError = ref<string | null>(null);

const search = useMutation({
  ...knowledgeSearchRunMutation(),
  onError: (failure) => {
    searchError.value = problemMessage(failure);
  },
});

function runSearch() {
  searchError.value = null;
  search.mutate({
    body: {
      query: query.value.trim(),
      limit: 5,
      as_of: asOf.value ? new Date(asOf.value).toISOString() : null,
    },
  });
}
</script>

<template>
  <PageHeader
    title="Search"
    description="Search the knowledge you may read. Results come from the live content unless you pick a past moment."
  />

  <SectionCard
    title="Ask the knowledge"
    description="Results follow your access rules and show the document and section they came from."
  >
    <form
      class="flex flex-wrap gap-3"
      @submit.prevent="runSearch"
    >
      <input
        v-model="query"
        name="query"
        required
        maxlength="500"
        aria-label="Search query"
        class="min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 py-2"
      >
      <label class="flex items-center gap-2 text-sm">
        <span>As of</span>
        <input
          v-model="asOf"
          type="datetime-local"
          name="as_of"
          class="rounded-lg border border-line bg-surface px-3 py-2"
        >
      </label>
      <button
        type="submit"
        class="rounded-lg bg-primary px-4 py-2 font-semibold text-primary-contrast"
        :disabled="search.isPending.value"
      >
        Search
      </button>
    </form>

    <p
      v-if="searchError"
      class="mt-4 text-sm text-muted"
      role="alert"
    >
      {{ searchError }}
    </p>

    <div
      v-if="search.data.value"
      class="mt-6 space-y-4"
    >
      <p
        v-if="search.data.value.as_of"
        class="text-sm text-muted"
      >
        Showing what was live at {{ search.data.value.as_of }}.
      </p>
      <p
        v-if="search.data.value.truncated"
        class="text-sm text-muted"
      >
        Some results were left out to stay within the token budget.
      </p>
      <p
        v-if="search.data.value.results.length === 0"
        class="text-sm text-muted"
      >
        No results you may read.
      </p>
      <article
        v-for="hit in search.data.value.results"
        :key="hit.chunk_ids.join(',')"
        class="rounded-lg border border-line p-4"
      >
        <p class="font-medium">
          {{ hit.title }}
        </p>
        <p class="text-sm text-muted">
          {{ hit.collection_name }} · {{ hit.heading_path }}
          <span v-if="hit.locator"> · {{ hit.locator }}</span>
          · {{ hit.language }}
        </p>
        <p class="mt-2 whitespace-pre-line text-sm">
          {{ hit.text }}
        </p>
      </article>
    </div>
  </SectionCard>
</template>
