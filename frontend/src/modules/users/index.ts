import type { RouteRecordRaw } from 'vue-router';

import HomePage from './HomePage.vue';
import SignedOutPage from './SignedOutPage.vue';

export { useSignOut } from './useSignOut';

// The pages the users module owns. The router mounts them; the signed-out page is shown without the shell.
export const usersRoutes: RouteRecordRaw[] = [
  { path: '/', name: 'home', component: HomePage },
  { path: '/signed-out', name: 'signed-out', component: SignedOutPage, meta: { layout: 'bare' } },
];
