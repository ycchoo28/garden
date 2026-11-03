// Where the browser goes when a page needs a signed-in person. Sign-in starts on the API, which sends
// the browser to the identity provider and back. The router, the query client and the signed-out page
// all read these rules from here, so a path or a sign-in address changes in one place.
export const SIGNED_OUT = '/signed-out';

// Pages anyone may open, signed in or not. A 401 on one of them leaves the person where they are.
const PUBLIC_PATHS: readonly string[] = [SIGNED_OUT];

export function isPublicPath(path: string): boolean {
  return PUBLIC_PATHS.includes(path);
}

// The address that starts sign-in, and then returns the person to `returnTo`.
export function signInAddress(returnTo: string): string {
  return `/auth/login?return_to=${encodeURIComponent(returnTo)}`;
}

// Where a 401 on the page sends the browser, or null when the page is public and it stays.
export function unauthorizedRedirect(path: string, search: string): string | null {
  if (isPublicPath(path)) return null;
  return signInAddress(`${path}${search}`);
}
