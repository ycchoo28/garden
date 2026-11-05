import type { ModuleNavigation } from '../../shared/ui';
import { CLAIMS_DECIDE_PERMISSION, CLAIMS_VIEW_PERMISSION } from './access';

// The claims module's links in the header. The shell hides each link from a person without its permission.
export const claimsNavigation: ModuleNavigation = {
  label: 'Claims',
  items: [
    { label: 'Claims', to: '/claims', permission: CLAIMS_VIEW_PERMISSION },
    { label: 'Approvals', to: '/approvals', permission: CLAIMS_DECIDE_PERMISSION },
  ],
};
