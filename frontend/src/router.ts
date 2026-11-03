import { createRouter, createWebHistory } from 'vue-router';

import { usersSelfReadOptions } from './api/generated/@tanstack/vue-query.gen';
import { usersRoutes } from './modules/users';
import { queryClient } from './queryClient';
import { isUnauthorized } from './shared/problem';
import { isPublicPath, signInAddress } from './shared/redirects';

export const router = createRouter({
  history: createWebHistory(),
  routes: [...usersRoutes, { path: '/:pathMatch(.*)*', redirect: '/' }],
});

// A private page needs a signed-in person. Without a session the browser goes to sign-in and comes back here.
router.beforeEach(async (to) => {
  if (isPublicPath(to.path)) return true;
  try {
    await queryClient.ensureQueryData(usersSelfReadOptions());
    return true;
  } catch (error) {
    if (!isUnauthorized(error)) return true;
    window.location.assign(signInAddress(to.fullPath));
    return false;
  }
});
