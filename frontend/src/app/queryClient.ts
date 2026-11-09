import { MutationCache, QueryCache, QueryClient } from '@tanstack/vue-query';

import { isUnauthorized } from '../shared/problem';
import { unauthorizedRedirect } from '../shared/redirects';

// A session that has ended answers 401 to every request. A page that is already open then sends the
// person to sign in again, as a navigation does (router.ts), instead of showing an error. A public
// page stays where it is (shared/redirects.ts).
function signInAgainOnUnauthorized(error: unknown): void {
  if (!isUnauthorized(error)) return;
  const { pathname, search } = window.location;
  const address = unauthorizedRedirect(pathname, search);
  if (address !== null) window.location.assign(address);
}

// A 401 is an answer, not a fault, so it is never retried. The sign-in redirect then happens at once.
function retryUnlessUnauthorized(failureCount: number, error: unknown): boolean {
  if (isUnauthorized(error)) return false;
  return failureCount < 3;
}

export const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: retryUnlessUnauthorized } },
  queryCache: new QueryCache({ onError: signInAgainOnUnauthorized }),
  mutationCache: new MutationCache({ onError: signInAgainOnUnauthorized }),
});
