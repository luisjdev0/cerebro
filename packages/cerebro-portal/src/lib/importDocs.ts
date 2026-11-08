import { ApiError } from './api';
import { createDocument, getDocument, replaceDocument } from './documents';
import { slugify } from './memories';

/** One document ready to be created, read from an uploaded file. */
export type ImportItem = { path: string; title: string; slug: string; content: string };
export type ReadResult = { items: ImportItem[]; ignored: string[]; single: boolean };

export const MAX_FILE_BYTES = 2 * 1024 * 1024;
export const ACCEPT = '.md,.markdown,.txt';

const TEXT_EXT = /\.(md|markdown|txt)$/i;
const TITLE_RE = /^#{1}\s+(.+?)\s*#*\s*$/m;

/** Same rule as `cerebro docs import-markdown`: the title is the first `# heading` (else the file
 *  name) and the slug comes from the file name, so it stays traceable to the source file. */
export function titleAndSlug(path: string, text: string): { title: string; slug: string } {
  const stem = (path.split('/').pop() ?? path).replace(/\.[^.]+$/, '');
  const m = TITLE_RE.exec(text);
  return { title: (m?.[1] ?? stem).trim().slice(0, 300) || stem, slug: slugify(stem) || 'documento' };
}

const decode = (b: Uint8Array) => new TextDecoder('utf-8').decode(b).replace(/^﻿/, '');

/** Reads dropped/chosen files (.md and .txt) into import items; anything else is listed as ignored. */
export async function readFiles(files: File[]): Promise<ReadResult> {
  const raw: { path: string; text: string }[] = [];
  const ignored: string[] = [];
  for (const f of files) {
    if (TEXT_EXT.test(f.name) && !f.name.startsWith('.')) {
      if (f.size > MAX_FILE_BYTES) ignored.push(`${f.name} (>${MAX_FILE_BYTES / 1024 / 1024} MB)`);
      else raw.push({ path: f.name, text: (await f.text()).replace(/^\uFEFF/, '') });
    } else {
      ignored.push(f.name);
    }
  }
  const taken = new Set<string>();
  const items: ImportItem[] = [];
  for (const r of raw) {
    if (!r.text.trim()) {
      ignored.push(`${r.path} (vacío)`);
      continue;
    }
    const { title, slug } = titleAndSlug(r.path, r.text);
    let unique = slug;
    for (let n = 2; taken.has(unique); n++) unique = `${slug}-${n}`.slice(0, 100); // two files with the same name
    taken.add(unique);
    items.push({ path: r.path, title, slug: unique, content: r.text });
  }
  return { items, ignored, single: files.length === 1 && items.length === 1 };
}

export type ImportOutcome = { item: ImportItem; status: 'created' | 'updated' | 'skipped' | 'failed'; error?: string };

/** Creates the documents one by one (there is no bulk endpoint, same as the CLI importer).
 *  `existing` decides what happens when the category already has that identifier. */
export async function importItems(
  items: ImportItem[],
  category: string,
  existing: 'skip' | 'update',
  errText: (e: unknown) => string,
  onProgress: (done: number, total: number) => void,
): Promise<ImportOutcome[]> {
  const out: ImportOutcome[] = [];
  for (const item of items) {
    try {
      let found: { id: string } | null = null;
      try {
        found = await getDocument(category, item.slug);
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 404)) throw e;
      }
      if (found && existing === 'skip') out.push({ item, status: 'skipped' });
      else if (found) {
        await replaceDocument(found.id, { title: item.title, content: item.content, category, slug: item.slug });
        out.push({ item, status: 'updated' });
      } else {
        await createDocument({ title: item.title, content: item.content, category, slug: item.slug });
        out.push({ item, status: 'created' });
      }
    } catch (e) {
      out.push({ item, status: 'failed', error: errText(e) });
    }
    onProgress(out.length, items.length);
  }
  return out;
}
