// The shared UI kit. Screens import from here; docs/ui.md says when to use each piece.
export { default as AppShell } from './AppShell.vue';
export { default as BareLayout } from './BareLayout.vue';
export { default as PageHeader } from './PageHeader.vue';
export { default as SectionCard } from './SectionCard.vue';
export { default as SkipLink } from './SkipLink.vue';
export { useColorMode } from './colorMode';
export { navigationFor, type ModuleNavigation, type Navigation, type NavItem } from './navigation';
export type { ShellPerson } from './person';
export { themeOptions } from './theme';
