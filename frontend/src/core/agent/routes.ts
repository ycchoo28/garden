import type { RouteRecordRaw } from 'vue-router';

// The agent pages: a person's command line sign-ins, the actions the command asks them to approve, and their model usage.
export const agentRoutes: RouteRecordRaw[] = [
  { path: '/agent/logins', name: 'agent-logins', component: () => import('./CliLoginsPage.vue') },
  // The page a person opens from a link the command prints, to approve or decline one action.
  { path: '/agent/confirmations/:id', name: 'agent-confirmation', component: () => import('./ConfirmPage.vue') },
  { path: '/agent/usage', name: 'agent-usage', component: () => import('./UsagePage.vue') },
];
