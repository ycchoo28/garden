<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query';
import { computed } from 'vue';
import { RouterLink } from 'vue-router';

import { usersSelfReadOptions } from '../../api/generated/@tanstack/vue-query.gen';
import PageHeader from '../../shared/ui/PageHeader.vue';
import SectionCard from '../../shared/ui/SectionCard.vue';

const { data: me } = useQuery(usersSelfReadOptions());
const permissions = computed(() => me.value?.permissions ?? []);
const roles = computed(() => me.value?.roles ?? []);
</script>

<template>
  <PageHeader
    :title="me ? `Welcome, ${me.name}` : 'Welcome'"
    :description="me ? `${me.department.name} · ${me.email}` : undefined"
  />
  <div class="grid gap-6 lg:grid-cols-2">
    <SectionCard
      title="Your permissions"
      description="What the portal lets you do. Each one is checked on the server for every request."
    >
      <ul class="space-y-2">
        <li
          v-for="permission in permissions"
          :key="permission"
        >
          <code class="rounded bg-surface-muted px-1.5 py-0.5 font-mono text-sm">{{ permission }}</code>
        </li>
      </ul>
    </SectionCard>
    <SectionCard
      title="Your roles"
      description="Roles grant permissions. Everyone who signs in is an employee."
    >
      <ul class="space-y-2">
        <li
          v-for="role in roles"
          :key="role"
        >
          <span class="font-medium">{{ role }}</span>
        </li>
      </ul>
    </SectionCard>
    <SectionCard
      title="Your tools"
      description="Where you manage the garden command and the model gateway."
    >
      <ul class="space-y-2">
        <li>
          <RouterLink
            class="font-medium text-primary underline-offset-4 hover:underline"
            to="/cli-logins"
          >
            Command line sign-ins
          </RouterLink>
        </li>
        <li>
          <RouterLink
            class="font-medium text-primary underline-offset-4 hover:underline"
            to="/usage"
          >
            Model usage today
          </RouterLink>
        </li>
      </ul>
    </SectionCard>
  </div>
</template>
