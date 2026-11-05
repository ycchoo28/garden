import { describe, expect, it } from 'vitest';

import { type ModuleNavigation, navigationFor } from './navigation';

const claims: ModuleNavigation = {
  label: 'Claims',
  items: [
    { label: 'Claims', to: '/claims', permission: 'claims.view' },
    { label: 'Approvals', to: '/approvals', permission: 'claims.decide' },
  ],
};

const budgets: ModuleNavigation = {
  label: 'Budgets',
  items: [{ label: 'Variance', to: '/budgets/variance', permission: 'budgets.view' }],
};

const open: ModuleNavigation = {
  label: 'Open',
  items: [{ label: 'Help', to: '/help' }],
};

describe('navigationFor', () => {
  it('drops the items the person has no permission for', () => {
    const links = navigationFor([claims], ['claims.view']);

    expect(links).toEqual([{ label: 'Claims', items: [{ label: 'Claims', to: '/claims', permission: 'claims.view' }] }]);
  });

  it('keeps the items that need no permission', () => {
    expect(navigationFor([open], [])).toEqual([open]);
  });

  it('drops a module left with no items', () => {
    expect(navigationFor([claims, budgets], ['budgets.view'])).toEqual([
      { label: 'Budgets', items: [{ label: 'Variance', to: '/budgets/variance', permission: 'budgets.view' }] },
    ]);
  });

  it('keeps the modules and their items in the order given', () => {
    const links = navigationFor([budgets, open, claims], ['claims.view', 'claims.decide', 'budgets.view']);

    expect(links.map((module) => module.label)).toEqual(['Budgets', 'Open', 'Claims']);
    expect(links[2]?.items.map((item) => item.label)).toEqual(['Claims', 'Approvals']);
  });

  it('does not change the modules it is given', () => {
    navigationFor([claims], []);

    expect(claims.items).toHaveLength(2);
  });
});
