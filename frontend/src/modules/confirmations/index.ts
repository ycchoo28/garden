import type { RouteRecordRaw } from 'vue-router';

import ConfirmPage from './ConfirmPage.vue';

// The page a person opens from a link the garden command prints, to approve or decline one action.
export const confirmationsRoutes: RouteRecordRaw[] = [
  { path: '/confirm/:id', name: 'confirm', component: ConfirmPage },
];
