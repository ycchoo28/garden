import type { RouteRecordRaw } from 'vue-router';

// The knowledge module's pages. Each page loads the first time its route is visited.
export const knowledgeRoutes: RouteRecordRaw[] = [
  { path: '/collections', name: 'collections', component: () => import('./collections/CollectionsPage.vue') },
  { path: '/batches/:batchId', name: 'batch', component: () => import('./batches/BatchPage.vue'), props: true },
  { path: '/search', name: 'search', component: () => import('./search/SearchPage.vue') },
  { path: '/search-log', name: 'search-log', component: () => import('./search/SearchLogPage.vue') },
];
