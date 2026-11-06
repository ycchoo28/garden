<script setup lang="ts">
import SkipLink from '../../shared/ui/SkipLink.vue';

// The layout for pages shown without a session, such as the signed-out page: the name of the app and nothing else.
</script>

<template>
  <SkipLink />
  <header class="border-b border-line bg-surface">
    <div class="mx-auto flex h-14 max-w-[70rem] items-center gap-2.5 px-4">
      <span
        class="grid size-8 place-items-center rounded-lg bg-primary font-bold text-primary-contrast"
        aria-hidden="true"
      >G</span>
      <span class="font-semibold">Garden</span>
    </div>
  </header>
  <main
    id="main"
    tabindex="-1"
    class="px-4 py-10 outline-none"
  >
    <div class="mx-auto max-w-xl">
      <slot />
    </div>
  </main>
</template>
