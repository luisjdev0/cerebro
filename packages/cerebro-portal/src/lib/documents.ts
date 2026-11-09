import { api } from './api';

export type DocStatus = 'active' | 'archived';
export type DocSortKey = 'updated_at' | 'created_at' | 'title' | 'category';

export type DocumentSummary = {
  id: string;
  category: string;
  slug: string;
  title: string;
  excerpt: string;
  status: DocStatus | string;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  score?: number | null;
};
export type Doc = Omit<DocumentSummary, 'excerpt'> & { content: string; redirected_from?: { category: string; slug: string } | null };
export type DocVersion = { version_number: number; category: string; title: string; content: string; created_at: string };
export type Category = { id: string; slug: string; name: string; description: string | null; hidden: boolean; locked: boolean };

export type DocListParams = {
  category?: string;
  q?: string;
  status: DocStatus | 'all';
  sort: DocSortKey;
  order: 'asc' | 'desc';
  limit: number;
  offset: number;
};

function qs(params: Record<string, string | number | undefined>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== '') u.set(k, String(v));
  return u.toString();
}

export const browseDocuments = (p: DocListParams) =>
  api<{ items: DocumentSummary[]; total: number }>(`/docs/documents/browse?${qs({ ...p })}`);
export const getDocument = (category: string, slug: string) => api<Doc>(`/docs/documents/${encodeURIComponent(category)}/${encodeURIComponent(slug)}`);
export const getVersions = (id: string) => api<DocVersion[]>(`/docs/documents/${id}/versions`);

export type DocInput = { title: string; content: string; category: string; slug?: string };
export const createDocument = (d: DocInput) => api<Doc>('/docs/documents', { method: 'POST', json: d });
/** Full replacement: the API snapshots the previous state as a version and can move the document to another category. */
export const replaceDocument = (id: string, d: DocInput) => api<Doc>(`/docs/documents/${id}`, { method: 'PATCH', json: d });
export const archiveDocument = (id: string) => api<Doc>(`/docs/documents/${id}/archive`, { method: 'POST' });
export const unarchiveDocument = (id: string) => api<Doc>(`/docs/documents/${id}/unarchive`, { method: 'POST' });
export const deleteDocument = (id: string) => api<unknown>(`/docs/documents/${id}`, { method: 'DELETE' });

export const listCategories = () => api<Category[]>('/docs/categories');
export const createCategory = (c: { slug: string; name: string; description?: string; hidden?: boolean }) =>
  api<Category>('/docs/categories', { method: 'POST', json: c });
export const updateCategory = (slug: string, c: { name?: string; description?: string; hidden?: boolean }) =>
  api<Category>(`/docs/categories/${slug}`, { method: 'PATCH', json: c });
export const deleteCategory = (slug: string, force: boolean) =>
  api<{ slug: string; status: string; documents_deleted: number }>(`/docs/categories/${slug}?force=${force}`, { method: 'DELETE' });
export const countCategoryDocuments = (slug: string) =>
  browseDocuments({ category: slug, status: 'all', sort: 'updated_at', order: 'desc', limit: 1, offset: 0 }).then((r) => r.total);

/** Turns the stored markdown excerpt into one plain line for the table. */
export const plainExcerpt = (md: string): string =>
  md
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/[#>*_`~|-]+/g, ' ')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\s+/g, ' ')
    .trim();
