// Public interface of the spend module. The router mounts these pages; nothing else imports into this module.
import type { RouteRecordRaw } from 'vue-router';

import StatementsPage from './StatementsPage.vue';

export const spendRoutes: RouteRecordRaw[] = [
  { path: '/spend', name: 'spend', component: StatementsPage },
];
