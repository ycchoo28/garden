import type { RouteRecordRaw } from 'vue-router';

// Global home and signed-out pages belong to the application; Users retains session operations.
export const appRoutes: RouteRecordRaw[] = [
  { path: '/', name: 'home', component: () => import('./HomePage.vue') },
  { path: '/signed-out', name: 'signed-out', component: () => import('./SignedOutPage.vue'), meta: { layout: 'bare' } },
];
