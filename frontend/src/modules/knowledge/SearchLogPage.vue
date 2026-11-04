<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query';
import { computed } from 'vue';

import { knowledgeSearchLogListOptions } from '../../api/generated/@tanstack/vue-query.gen';
import { problemMessage } from '../../shared/problem';
import PageHeader from '../../shared/ui/PageHeader.vue';
import SectionCard from '../../shared/ui/SectionCard.vue';

// The log is written by the server for every search. This page only reads it.
const logQuery = useQuery(knowledgeSearchLogListOptions({ query: { limit: 100 } }));
const rows = computed(() => logQuery.data.value ?? []);
</script>

<template>
  <PageHeader
    title="Search log"
    description="Every search, who ran it, and whether an agent ran it for them. Only people with the log permission see this page."
  />

  <p
    v-if="logQuery.error.value"
    class="text-sm text-muted"
    role="alert"
  >
    {{ problemMessage(logQuery.error.value) }}
  </p>

  <SectionCard
    v-else
    title="Recent searches"
    description="The newest 100 searches."
    flush
  >
    <ul
      v-if="rows.length"
      class="divide-y divide-line"
    >
      <li
        v-for="row in rows"
        :key="row.id"
        class="px-5 py-3"
      >
        <p class="font-medium">
          {{ row.query }}
        </p>
        <p class="text-sm text-muted">
          {{ row.searched_at }} · {{ row.person_name }}
          <span v-if="row.acting_as_agent"> · run by an agent</span>
          <span v-if="row.as_of"> · as of {{ row.as_of }}</span>
          · {{ row.chunk_ids.length }} result(s)
        </p>
      </li>
    </ul>
    <p
      v-else
      class="px-5 py-4 text-sm text-muted"
    >
      No searches yet.
    </p>
  </SectionCard>
</template>
