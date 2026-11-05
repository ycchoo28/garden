import type { RouteRecordRaw } from 'vue-router';

// The pages the users core module owns, including the devices signed in with the garden command.
// The router mounts them; the signed-out page is shown without the shell.
export const usersRoutes: RouteRecordRaw[] = [
  { path: '/', name: 'home', component: () => import('./HomePage.vue') },
  { path: '/signed-out', name: 'signed-out', component: () => import('./SignedOutPage.vue'), meta: { layout: 'bare' } },
];
