// Public interface of the knowledge module. The app and other modules import only from here.
import type { RouteRecordRaw } from 'vue-router';

import { knowledgeStatusReadOptions } from '../../api/generated/@tanstack/vue-query.gen';
import BatchPage from './BatchPage.vue';
import CollectionsPage from './CollectionsPage.vue';
import SearchLogPage from './SearchLogPage.vue';
import SearchPage from './SearchPage.vue';

export const knowledgeStatusQuery = knowledgeStatusReadOptions;

// The pages the knowledge module owns. The router mounts them inside the app shell.
export const knowledgeRoutes: RouteRecordRaw[] = [
  { path: '/collections', name: 'collections', component: CollectionsPage },
  { path: '/batches/:batchId', name: 'batch', component: BatchPage, props: true },
  { path: '/search', name: 'search', component: SearchPage },
  { path: '/search-log', name: 'search-log', component: SearchLogPage },
];
