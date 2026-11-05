import type { RouteRecordRaw } from 'vue-router';

// The claims module's pages. Each page loads the first time its route is visited.
export const claimsRoutes: RouteRecordRaw[] = [
  { path: '/claims', name: 'claims', component: () => import('./drafting/ClaimsListPage.vue') },
  { path: '/claims/:id', name: 'claim', component: () => import('./drafting/ClaimDetailPage.vue'), props: true },
  { path: '/approvals', name: 'approvals', component: () => import('./review/ApprovalQueuePage.vue') },
];
