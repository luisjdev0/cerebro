import { describe, expect, it } from 'vitest';
import { slugify } from './memories';

describe('slugify', () => {
  it('lowercases, strips accents and collapses separators', () => {
    expect(slugify('  Ecosistema Cerebro!! ')).toBe('ecosistema-cerebro');
    expect(slugify('Facturación & Pagos')).toBe('facturacion-pagos');
  });
  it('caps the length at 100', () => {
    expect(slugify('a'.repeat(300))).toHaveLength(100);
  });
});
