import { useMutation, useQueryClient } from '@tanstack/vue-query';

import { usersSessionEndMutation } from '../../api/generated/@tanstack/vue-query.gen';
import { SIGNED_OUT } from '../../shared/redirects';

// Ends the session on the API, then loads the signed-out page with nothing from the old session kept.
export function useSignOut() {
  const queryClient = useQueryClient();
  return useMutation({
    ...usersSessionEndMutation(),
    onSuccess: () => {
      queryClient.clear();
      window.location.assign(SIGNED_OUT);
    },
  });
}
