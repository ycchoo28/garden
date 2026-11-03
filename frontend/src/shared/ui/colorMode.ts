// Light or dark. With no choice saved the portal follows the system, and a choice is kept per browser.
// public/color-mode.js applies the same rule before the first paint.
import { computed, ref } from 'vue';

export type ColorMode = 'light' | 'dark';

const STORAGE_KEY = 'garden-color-mode';
const systemQuery = window.matchMedia('(prefers-color-scheme: dark)');

function readSaved(): ColorMode | null {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    return saved === 'light' || saved === 'dark' ? saved : null;
  } catch {
    return null;
  }
}

const saved = ref<ColorMode | null>(readSaved());
const systemDark = ref(systemQuery.matches);
systemQuery.addEventListener('change', (event) => {
  systemDark.value = event.matches;
  apply();
});

const mode = computed<ColorMode>(() => saved.value ?? (systemDark.value ? 'dark' : 'light'));

function apply() {
  document.documentElement.classList.toggle('app-dark', mode.value === 'dark');
}

export function useColorMode() {
  function toggle() {
    saved.value = mode.value === 'dark' ? 'light' : 'dark';
    try {
      window.localStorage.setItem(STORAGE_KEY, saved.value);
    } catch {
      // Private windows may refuse storage. The choice then lasts until the page closes.
    }
    apply();
  }
  return { mode, toggle };
}
