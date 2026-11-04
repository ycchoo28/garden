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

// A link shows only when the person holds the permission it leads to. The server checks every request anyway.
const links = computed(() => {
  const permissions = me.value?.permissions ?? [];
  const items: { to: string; label: string }[] = [];
  if (permissions.includes('claims.view')) items.push({ to: '/claims', label: 'Claims' });
  if (permissions.includes('claims.decide')) items.push({ to: '/approvals', label: 'Approvals' });
  if (permissions.includes('budgets.view')) items.push({ to: '/budgets/variance', label: 'Variance' });
  return items;
});

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
    :links="links"
    @sign-out="signOut.mutate({})"
  >
    <RouterView />
  </AppShell>
</template>
