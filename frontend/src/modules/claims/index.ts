// Public interface of the claims module. The router mounts these pages; other modules import nothing from here.
import type { RouteRecordRaw } from 'vue-router';

import ApprovalQueuePage from './ApprovalQueuePage.vue';
import ClaimDetailPage from './ClaimDetailPage.vue';
import ClaimsListPage from './ClaimsListPage.vue';

export const claimsRoutes: RouteRecordRaw[] = [
  { path: '/claims', name: 'claims', component: ClaimsListPage },
  { path: '/claims/:id', name: 'claim', component: ClaimDetailPage, props: true },
  { path: '/approvals', name: 'approvals', component: ApprovalQueuePage },
];
