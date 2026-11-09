import { createRouter, createWebHistory } from "vue-router";

import { usersSelfReadOptions } from "../api/generated/@tanstack/vue-query.gen";
import { agentRoutes } from "../core/agent";
import { budgetsRoutes } from "../modules/budgets";
import { claimsRoutes } from "../modules/claims";
import { knowledgeRoutes } from "../modules/knowledge";
import { isUnauthorized } from "../shared/problem";
import { isPublicPath, signInAddress } from "../shared/redirects";
import { queryClient } from "./queryClient";
import { appRoutes } from "./routes";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    ...appRoutes,
    ...claimsRoutes,
    ...budgetsRoutes,
    ...knowledgeRoutes,
    ...agentRoutes,
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
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
