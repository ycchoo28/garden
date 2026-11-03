// The signed-in person as the shell shows them. App.vue fills it from the users module.
export interface ShellPerson {
  name: string;
  email: string;
}

// Up to two initials for the avatar, such as "DL" for "Daniel Lee".
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const first = parts[0]?.[0] ?? '';
  const last = parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? '') : '';
  return `${first}${last}`.toUpperCase();
}
