<script setup lang="ts">
import { useColorMode } from './colorMode';
import { initials, type ShellPerson } from './person';
import SkipLink from './SkipLink.vue';

// The frame for a signed-in page: the brand, the links the person may use, the person, sign-out and the
// colour choice. Pages fill the slot.
defineProps<{
  person?: ShellPerson;
  signingOut?: boolean;
  links?: { to: string; label: string }[];
}>();

const emit = defineEmits<{
  signOut: [];
}>();

const { mode, toggle } = useColorMode();
</script>

<template>
  <SkipLink />
  <header class="border-b border-line bg-surface">
    <div class="mx-auto flex h-14 max-w-[70rem] items-center gap-3 px-4">
      <RouterLink
        to="/"
        class="flex shrink-0 items-center gap-2.5 rounded-lg"
      >
        <span
          class="grid size-8 place-items-center rounded-lg bg-primary font-bold text-primary-contrast"
          aria-hidden="true"
        >G</span>
        <span class="font-semibold">Garden</span>
      </RouterLink>
      <nav
        v-if="links?.length"
        aria-label="Main"
        class="flex min-w-0 items-center gap-1 overflow-x-auto"
      >
        <RouterLink
          v-for="link in links"
          :key="link.to"
          :to="link.to"
          class="rounded-lg px-3 py-1.5 text-sm font-medium whitespace-nowrap text-muted hover:text-fg"
          active-class="bg-primary-soft text-primary-fg"
        >
          {{ link.label }}
        </RouterLink>
      </nav>
      <div class="ml-auto flex min-w-0 items-center gap-3">
        <button
          type="button"
          class="rounded-lg border border-line px-3 py-1.5 text-sm font-medium"
          :aria-label="mode === 'dark' ? 'Use light colours' : 'Use dark colours'"
          @click="toggle"
        >
          {{ mode === 'dark' ? 'Light' : 'Dark' }}
        </button>
        <template v-if="person">
          <span
            class="grid size-8 shrink-0 place-items-center rounded-full border border-line text-sm font-semibold"
            aria-hidden="true"
          >{{ initials(person.name) }}</span>
          <span class="max-w-[12rem] truncate text-sm font-medium">{{ person.name }}</span>
          <button
            type="button"
            class="rounded-lg border border-line px-3 py-1.5 text-sm font-medium disabled:opacity-60"
            :disabled="signingOut"
            @click="emit('signOut')"
          >
            Sign out
          </button>
        </template>
      </div>
    </div>
  </header>
  <main
    id="main"
    tabindex="-1"
    class="mx-auto max-w-[70rem] px-4 py-8 outline-none lg:py-10"
  >
    <slot />
  </main>
</template>
