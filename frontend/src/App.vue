<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query';
import { computed } from 'vue';
import { RouterView, useRoute } from 'vue-router';

import { usersSelfReadOptions } from './api/generated/@tanstack/vue-query.gen';
import { useSignOut } from './modules/users';
import AppShell from './shared/ui/AppShell.vue';
import BareLayout from './shared/ui/BareLayout.vue';

const route = useRoute();
const bare = computed(() => route.meta.layout === 'bare');

const { data: me } = useQuery({ ...usersSelfReadOptions(), enabled: computed(() => !bare.value) });
const person = computed(() => (me.value ? { name: me.value.name, email: me.value.email } : undefined));

const signOut = useSignOut();
</script>

<template>
  <BareLayout v-if="bare">
    <RouterView />
  </BareLayout>
  <AppShell
    v-else
    :person="person"
    :signing-out="signOut.isPending.value"
    @sign-out="signOut.mutate({})"
  >
    <RouterView />
  </AppShell>
</template>
