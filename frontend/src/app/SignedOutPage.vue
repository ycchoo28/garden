<script setup lang="ts">
import { signInAddress } from '../shared/redirects';
</script>

<template>
  <section class="mx-auto max-w-md py-12 text-center">
    <h1 class="text-2xl font-semibold tracking-tight">
      You are signed out
    </h1>
    <p class="mt-2 text-muted">
      Your session has ended. Sign in again to continue.
    </p>
    <a
      class="mt-6 inline-flex rounded-lg bg-primary px-4 py-2 font-semibold text-primary-contrast"
      :href="signInAddress('/')"
    >Sign in</a>
  </section>
</template>
