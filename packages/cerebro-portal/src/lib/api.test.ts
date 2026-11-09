import { describe, expect, it } from 'vitest';
import { API_PREFIX, apiUrl } from './api';

describe('apiUrl', () => {
  it('puts every service call under the gateway /api prefix', () => {
    expect(API_PREFIX).toBe('/api');
    expect(apiUrl('/memory/contexts')).toBe('/api/memory/contexts');
    expect(apiUrl('/auth/login/password')).toBe('/api/auth/login/password');
  });
});
