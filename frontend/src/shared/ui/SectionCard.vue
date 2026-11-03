<script setup lang="ts">
import { useId } from 'vue';

// A titled card. It is a region named by its title, so a screen reader and a browser test can find it by name.
withDefaults(
  defineProps<{
    title: string;
    description?: string;
    // The heading level, 2 on most pages.
    level?: 2 | 3;
    // Rows that run edge to edge, such as a list with dividers, need no padding around the body.
    flush?: boolean;
  }>(),
  { description: undefined, level: 2, flush: false },
);

const headingId = useId();
</script>

<template>
  <section
    class="rounded-xl border border-line bg-surface shadow-card"
    :aria-labelledby="headingId"
  >
    <div class="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line px-5 py-4">
      <div class="min-w-0">
        <component
          :is="`h${level}`"
          :id="headingId"
          class="text-base font-semibold"
        >
          {{ title }}
        </component>
        <p
          v-if="description"
          class="text-sm text-muted"
        >
          {{ description }}
        </p>
      </div>
      <div
        v-if="$slots.actions"
        class="ml-auto flex items-center gap-2"
      >
        <slot name="actions" />
      </div>
    </div>
    <div :class="flush ? '' : 'p-5'">
      <slot />
    </div>
    <div
      v-if="$slots.footer"
      class="border-t border-line px-5 py-3"
    >
      <slot name="footer" />
    </div>
  </section>
</template>
