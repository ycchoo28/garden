<script setup lang="ts">
import { computed } from 'vue';

import { statusLabel } from './format';

const props = defineProps<{ status: string }>();

// Each status keeps its colour and its words, so the status is clear without colour.
const tone = computed(() => {
  switch (props.status) {
    case 'approved':
      return 'border-success-line bg-success-soft text-success-fg';
    case 'rejected':
      return 'border-danger-line bg-danger-soft text-danger-fg';
    case 'submitted':
      return 'border-info-line bg-info-soft text-info-fg';
    default:
      return 'border-line bg-surface-muted text-muted';
  }
});
</script>

<template>
  <span
    class="inline-flex items-center rounded-full border px-2.5 py-0.5 text-sm font-medium whitespace-nowrap"
    :class="tone"
  >
    {{ statusLabel(status) }}
  </span>
</template>
