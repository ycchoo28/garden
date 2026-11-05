import type { ModuleNavigation } from '../../shared/ui';
import { BUDGETS_MONTH_CLOSE_PERMISSION, BUDGETS_VIEW_PERMISSION } from './access';

// The budgets module's links in the header. The shell hides each link from a person without its permission.
export const budgetsNavigation: ModuleNavigation = {
  label: 'Budgets',
  items: [
    { label: 'Variance', to: '/budgets/variance', permission: BUDGETS_VIEW_PERMISSION },
    { label: 'Month close', to: '/budgets/months', permission: BUDGETS_MONTH_CLOSE_PERMISSION },
  ],
};
