// Sets light or dark before the first paint, so a dark-mode visitor never sees a white flash.
// The choice is kept per browser by src/shared/ui/colorMode.ts. With no choice saved, it follows the system.
(function () {
  let saved = null;
  try {
    saved = window.localStorage.getItem('garden-color-mode');
  } catch {
    // Private windows may refuse storage. The page then follows the system.
  }
  const dark = saved === 'dark' || (saved !== 'light' && window.matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.classList.toggle('app-dark', dark);
})();
