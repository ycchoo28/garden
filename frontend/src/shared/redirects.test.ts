import { describe, expect, it } from 'vitest';

import { isPublicPath, signInAddress, unauthorizedRedirect } from './redirects';

describe('redirects', () => {
  it('keeps the signed-out page public', () => {
    expect(isPublicPath('/signed-out')).toBe(true);
    expect(isPublicPath('/')).toBe(false);
  });

  it('starts sign-in and returns the person to where they were', () => {
    expect(signInAddress('/?tab=1')).toBe('/auth/login?return_to=%2F%3Ftab%3D1');
  });

  it('sends a 401 on a private page to sign-in, and leaves a public page alone', () => {
    expect(unauthorizedRedirect('/', '')).toBe('/auth/login?return_to=%2F');
    expect(unauthorizedRedirect('/signed-out', '')).toBeNull();
  });
});
