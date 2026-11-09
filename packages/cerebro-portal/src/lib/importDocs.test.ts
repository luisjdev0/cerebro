import { describe, expect, it } from 'vitest';
import { readFiles, titleAndSlug } from './importDocs';

const file = (name: string, data: BlobPart) => new File([data], name);

describe('titleAndSlug', () => {
  it('uses the first H1 as title and the file name as slug', () => {
    expect(titleAndSlug('guias/Mi_Guía Rápida.md', '# Hola mundo\n\ntexto')).toEqual({ title: 'Hola mundo', slug: 'mi-guia-rapida' });
  });
  it('falls back to the file name when there is no H1', () => {
    expect(titleAndSlug('notas.txt', 'sin título')).toEqual({ title: 'notas', slug: 'notas' });
  });
});

describe('readFiles', () => {
  it('reads one markdown file as a single document', async () => {
    const r = await readFiles([file('a.md', '# A\n\nx')]);
    expect(r.single).toBe(true);
    expect(r.items[0]).toMatchObject({ title: 'A', slug: 'a' });
  });
  it('reads several files, de-duplicates slugs and skips empty ones', async () => {
    const r = await readFiles([file('a.md', '# Uno'), file('A.txt', 'otro'), file('vacio.md', '  ')]);
    expect(r.single).toBe(false);
    expect(r.items.map((i) => i.slug)).toEqual(['a', 'a-2']);
    expect(r.ignored).toEqual(['vacio.md (vacío)']);
  });
  it('lists unsupported files (zip included) as ignored', async () => {
    const r = await readFiles([file('x.pdf', 'x'), file('y.zip', 'x')]);
    expect(r.items).toEqual([]);
    expect(r.ignored).toEqual(['x.pdf', 'y.zip']);
  });
});
