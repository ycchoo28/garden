import { describe, expect, it } from 'vitest';

import { type ModuleNavigation, navigationFor } from './navigation';

const inventory: ModuleNavigation = {
  label: 'Inventory',
  items: [
    { label: 'Inventory', to: '/inventory', permission: 'inventory-view' },
    { label: 'Catalog', to: '/catalog', permission: 'inventory-decide' },
  ],
};

const archive: ModuleNavigation = {
  label: 'Archive',
  items: [{ label: 'Monthly summary', to: '/archive/summary', permission: 'archive-view' }],
};

const open: ModuleNavigation = {
  label: 'Open',
  items: [{ label: 'Help', to: '/help' }],
};

describe('navigationFor', () => {
  it('drops the items the person has no permission for', () => {
    const links = navigationFor([inventory], ['inventory-view']);

    expect(links).toEqual([{ label: 'Inventory', items: [{ label: 'Inventory', to: '/inventory', permission: 'inventory-view' }] }]);
  });

  it('keeps the items that need no permission', () => {
    expect(navigationFor([open], [])).toEqual([open]);
  });

  it('drops a module left with no items', () => {
    expect(navigationFor([inventory, archive], ['archive-view'])).toEqual([
      { label: 'Archive', items: [{ label: 'Monthly summary', to: '/archive/summary', permission: 'archive-view' }] },
    ]);
  });

  it('keeps the modules and their items in the order given', () => {
    const links = navigationFor([archive, open, inventory], ['inventory-view', 'inventory-decide', 'archive-view']);

    expect(links.map((module) => module.label)).toEqual(['Archive', 'Open', 'Inventory']);
    expect(links[2]?.items.map((item) => item.label)).toEqual(['Inventory', 'Catalog']);
  });

  it('does not change the modules it is given', () => {
    navigationFor([inventory], []);

    expect(inventory.items).toHaveLength(2);
  });
});
