<script setup lang="ts">
import { useQuery } from "@tanstack/vue-query";
import { computed } from "vue";
import { RouterView, useRoute } from "vue-router";

import { usersSelfReadOptions } from "../api/generated/@tanstack/vue-query.gen";
import { useSignOut } from "../core/users";
import { budgetsNavigation } from "../modules/budgets";
import { claimsNavigation } from "../modules/claims";
import { knowledgeNavigation } from "../modules/knowledge";
import { navigationFor } from "../shared/ui";
import { AppShell, BareLayout } from "./layouts";

const route = useRoute();
const bare = computed(() => route.meta.layout === "bare");

const { data: me } = useQuery({
  ...usersSelfReadOptions(),
  enabled: computed(() => !bare.value),
});
const person = computed(() =>
  me.value ? { name: me.value.name, email: me.value.email } : undefined,
);

// Each module adds its links here. A link shows only when the person holds the permission it leads to,
// and the server checks every request anyway.
const links = computed(() =>
  navigationFor(
    [claimsNavigation, budgetsNavigation, knowledgeNavigation],
    me.value?.permissions ?? [],
  ).flatMap((module) => module.items),
);

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
