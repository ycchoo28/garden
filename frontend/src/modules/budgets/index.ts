// Public interface of the budgets module. The router mounts these pages; nothing else imports into this module.
import type { RouteRecordRaw } from 'vue-router';

import BudgetPage from './BudgetPage.vue';

export const budgetsRoutes: RouteRecordRaw[] = [
  { path: '/budgets', name: 'budgets', component: BudgetPage },
];
