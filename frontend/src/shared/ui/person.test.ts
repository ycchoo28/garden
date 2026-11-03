import { describe, expect, it } from 'vitest';

import { initials } from './person';

describe('initials', () => {
  it('uses the first letter of the first and last name', () => {
    expect(initials('Daniel Lee')).toBe('DL');
    expect(initials('  ada  ')).toBe('A');
    expect(initials('')).toBe('');
  });
});
