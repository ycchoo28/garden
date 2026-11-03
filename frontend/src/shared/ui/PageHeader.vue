<script setup lang="ts">
// The top of a page: its one h1, a line saying what the page is for, and the page's main actions.
defineProps<{
  title: string;
  description?: string;
}>();
</script>

<template>
  <div class="mb-6 flex flex-wrap items-end justify-between gap-x-6 gap-y-3 lg:mb-8">
    <div class="min-w-0">
      <h1 class="text-2xl font-semibold tracking-tight lg:text-[1.75rem]">
        {{ title }}
      </h1>
      <p
        v-if="description"
        class="mt-1 text-muted"
      >
        {{ description }}
      </p>
    </div>
    <div
      v-if="$slots.actions"
      class="flex flex-wrap items-center gap-2"
    >
      <slot name="actions" />
    </div>
  </div>
</template>
