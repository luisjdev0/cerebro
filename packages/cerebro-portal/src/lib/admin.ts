import { api, ApiError } from './api';

export type AccessLevel = 'user' | 'owner' | 'admin';
export type ModuleId = 'memory' | 'docs' | 'flows';
export const MODULES: ModuleId[] = ['memory', 'docs', 'flows'];
/** The key inside each module's scope that narrows it: memory by context, docs/flows by category. */
export const RESTRICTION_KEY: Record<ModuleId, 'contexts' | 'categories'> = { memory: 'contexts', docs: 'categories', flows: 'categories' };

export type ModuleScopes = Partial<Record<ModuleId, { contexts?: string[] | null; categories?: string[] | null }>>;

export type AdminUser = {
  id: string;
  name: string;
  email: string | null;
  access_level: AccessLevel;
  created_at: string;
  revoked_at: string | null;
  has_password: boolean;
  groups: string[];
};

export type Group = {
  id: string;
  slug: string;
  name: string;
  created_at: string;
  members: string[];
  allowed_modules: ModuleId[] | null;
  module_scopes: ModuleScopes;
};

export type ApiToken = {
  id: string;
  name: string;
  user_id: string | null;
  scopes: string[];
  access_level: AccessLevel | null;
  allowed_modules: ModuleId[] | null;
  module_scopes: ModuleScopes;
  created_at: string;
  revoked_at: string | null;
  expires_at: string | null;
};

export const MIN_PASSWORD_LENGTH = 10;

const enc = encodeURIComponent;

export const listUsers = () => api<AdminUser[]>('/auth/users');
export const createUser = (u: { name: string; email?: string; access_level: AccessLevel; password?: string }) =>
  api<AdminUser>('/auth/users', { method: 'POST', json: u });
export const updateUser = (name: string, patch: { email?: string; access_level?: AccessLevel; active?: boolean }) =>
  api<AdminUser>(`/auth/users/${enc(name)}`, { method: 'PATCH', json: patch });
export const setUserPassword = (name: string, password: string) =>
  api<void>(`/auth/users/${enc(name)}/password`, { method: 'POST', json: { password } });

export const listGroups = () => api<Group[]>('/auth/groups');
export const createGroup = (g: { slug: string; name: string }) => api<Group>('/auth/groups', { method: 'POST', json: g });
export const updateGroup = (slug: string, name: string) => api<Group>(`/auth/groups/${enc(slug)}`, { method: 'PATCH', json: { name } });
export const deleteGroup = (slug: string) => api<unknown>(`/auth/groups/${enc(slug)}`, { method: 'DELETE' });
export const addGroupMember = (slug: string, user: string) => api<unknown>(`/auth/groups/${enc(slug)}/members`, { method: 'POST', json: { user } });
export const removeGroupMember = (slug: string, user: string) => api<unknown>(`/auth/groups/${enc(slug)}/members/${enc(user)}`, { method: 'DELETE' });
export const setGroupScopes = (slug: string, allowed_modules: ModuleId[], module_scopes: ModuleScopes) =>
  api<unknown>(`/auth/groups/${enc(slug)}/scopes`, { method: 'POST', json: { allowed_modules, module_scopes } });

export const listTokens = () => api<ApiToken[]>('/auth/tokens');
export type TokenInput = {
  name: string;
  scopes: ('read' | 'write')[];
  user?: string;
  access_level?: AccessLevel;
  allowed_modules?: ModuleId[];
  module_scopes?: ModuleScopes;
};
export const createToken = (t: TokenInput) => api<ApiToken & { token: string }>('/auth/tokens', { method: 'POST', json: t });
export const revokeToken = (name: string) => api<ApiToken>(`/auth/tokens/${enc(name)}`, { method: 'DELETE' });

/** Session tokens (`session:<user>:<hex>`) are minted by every password login: noise in a token list. */
export const isSessionToken = (t: Pick<ApiToken, 'name'>) => t.name.startsWith('session:');

/** The API's 409 on removing the last admin, as a flag the screens translate. */
export const isLastAdminError = (e: unknown) => e instanceof ApiError && e.status === 409 && /last active admin/i.test(e.message);
