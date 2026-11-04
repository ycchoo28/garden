// Public interface of the budgets module. The router mounts these pages; nothing else imports into this module.
import type { RouteRecordRaw } from 'vue-router';

import BudgetPage from './BudgetPage.vue';
import VariancePage from './VariancePage.vue';

export const budgetsRoutes: RouteRecordRaw[] = [
  { path: '/budgets', name: 'budgets', component: BudgetPage },
  { path: '/budgets/variance', name: 'budgets-variance', component: VariancePage },
];
