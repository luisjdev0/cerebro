import { describe, expect, it } from 'vitest';
import { en } from './en';
import { es } from './es';

function keys(o: Record<string, unknown>, prefix = ''): string[] {
  return Object.entries(o).flatMap(([k, v]) =>
    typeof v === 'object' && v !== null ? keys(v as Record<string, unknown>, `${prefix}${k}.`) : [`${prefix}${k}`],
  );
}

describe('locales', () => {
  it('es has exactly the same keys as en', () => {
    expect(keys(es).sort()).toEqual(keys(en).sort());
  });
});
