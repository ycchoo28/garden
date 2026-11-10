// What a module adds to the header. Each module exports one ModuleNavigation from its index.ts,
// and App.vue hands them to the shell, so the shell never imports a module.
export interface NavItem {
  label: string;
  to: string;
  // A PrimeIcons class, such as 'pi pi-home'. The header shows no icons today, so this is optional.
  icon?: string;
  // Shown only to a person with this permission. The backend still refuses what the person may not do.
  permission?: string;
}

export interface ModuleNavigation {
  // The module's section label.
  label: string;
  // The section's links, in the order they show.
  items: NavItem[];
}

export type Navigation = ModuleNavigation[];

// The links for one person: items they lack the permission for are dropped, then any module left empty.
export function navigationFor(modules: ModuleNavigation[], permissions: readonly string[]): Navigation {
  const allowed = (item: NavItem) => !item.permission || permissions.includes(item.permission);
  return modules
    .map((module) => ({ ...module, items: module.items.filter(allowed) }))
    .filter((module) => module.items.length > 0);
}
