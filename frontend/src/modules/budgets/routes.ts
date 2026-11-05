import type { RouteRecordRaw } from 'vue-router';

// The budgets module's pages. Each page loads the first time its route is visited.
export const budgetsRoutes: RouteRecordRaw[] = [
  { path: '/budgets', name: 'budgets', component: () => import('./lines/BudgetPage.vue') },
  { path: '/budgets/variance', name: 'budgets-variance', component: () => import('./variance/VariancePage.vue') },
  { path: '/budgets/months', name: 'budgets-months', component: () => import('./months/MonthClosePage.vue') },
];
