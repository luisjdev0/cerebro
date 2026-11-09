import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Plus, Search } from 'lucide-react';
import { Badge, Button, PageHeader, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@alquimias/ixchel';
import { useTranslation } from 'react-i18next';
import { GroupDetail, NewGroupPanel } from '../components/GroupPanels';
import { LevelBadge, ModuleChips } from '../components/adminUi';
import { FilterField, SELECT } from '../components/flowUi';
import { useShortDate } from '../components/memoryUi';
import { NewUserPanel, UserDetail } from '../components/UserPanels';
import { useAuth } from '../lib/auth';
import { MODULES, RESTRICTION_KEY, listGroups, listUsers, type AdminUser, type Group } from '../lib/admin';

type Tab = 'users' | 'groups';

function Empty({ cols, message }: { cols: number; message: string }) {
  return (
    <TableRow>
      <TableCell colSpan={cols} className="py-10 text-center text-sm text-muted-foreground">{message}</TableCell>
    </TableRow>
  );
}

export function Access() {
  const { t } = useTranslation();
  const date = useShortDate();
  const { session } = useAuth();
  const [params, setParams] = useSearchParams();
  const tab: Tab = params.get('tab') === 'groups' ? 'groups' : 'users';
  const openUser = params.get('user');
  const openGroup = params.get('group');
  const setParam = (key: string, value: string | null) =>
    setParams((p) => {
      const n = new URLSearchParams(p);
      if (value) n.set(key, value);
      else n.delete(key);
      return n;
    });
  const setTab = (next: Tab) =>
    setParams(() => (next === 'groups' ? new URLSearchParams({ tab: 'groups' }) : new URLSearchParams()));

  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [error, setError] = useState(false);
  const [q, setQ] = useState('');
  const [status, setStatus] = useState<'active' | 'inactive' | 'all'>('active');
  const [newUser, setNewUser] = useState(false);
  const [newGroup, setNewGroup] = useState(false);

  const reload = useCallback(() => {
    setError(false);
    Promise.all([listUsers(), listGroups()]).then(
      ([u, g]) => {
        setUsers(u);
        setGroups(g);
      },
      () => setError(true),
    );
  }, []);
  useEffect(reload, [reload]);

  const shownUsers = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (users ?? []).filter(
      (u) =>
        (status === 'all' || (status === 'active') === (u.revoked_at === null)) &&
        (!needle || u.name.toLowerCase().includes(needle) || (u.email ?? '').toLowerCase().includes(needle)),
    );
  }, [users, q, status]);

  const selectedUser = users?.find((u) => u.name === openUser) ?? null;
  const selectedGroup = groups?.find((g) => g.slug === openGroup) ?? null;
  const loading = users === null || groups === null;

  const TabButton = ({ id, label, count }: { id: Tab; label: string; count?: number }) => (
    <button
      type="button"
      role="tab"
      aria-selected={tab === id}
      onClick={() => setTab(id)}
      className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors ${tab === id ? 'bg-foreground text-background' : 'text-muted-foreground hover:text-foreground'}`}
    >
      {label}
      {count !== undefined && <span className="text-xs opacity-70">{count}</span>}
    </button>
  );

  return (
    <>
      <PageHeader title={t('admin.access.title')} description={t('admin.access.description')} />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div role="tablist" className="inline-flex gap-1 rounded-xl border p-1">
          <TabButton id="users" label={t('admin.tabs.users')} count={users?.length} />
          <TabButton id="groups" label={t('admin.tabs.groups')} count={groups?.length} />
        </div>
        {tab === 'users' ? (
          <Button variant="primary" leadingIcon={Plus} onClick={() => setNewUser(true)}>{t('admin.users.new')}</Button>
        ) : (
          <Button variant="primary" leadingIcon={Plus} onClick={() => setNewGroup(true)}>{t('admin.groups.new')}</Button>
        )}
      </div>

      {error && <p role="alert" className="text-sm text-destructive-text">{t('admin.loadError')}</p>}

      {tab === 'users' ? (
        <div className="space-y-4 rounded-2xl border bg-card p-4">
          <div className="flex flex-wrap items-end gap-3">
            <label className="relative block min-w-56 flex-1">
              <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
              <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('admin.users.search')} className="h-9 w-full rounded-lg border bg-background pr-3 pl-9 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring" />
            </label>
            <div className="w-44">
              <FilterField label={t('admin.status')}>
                <select className={SELECT} value={status} onChange={(e) => setStatus(e.target.value as typeof status)}>
                  <option value="active">{t('admin.users.active')}</option>
                  <option value="inactive">{t('admin.users.inactive')}</option>
                  <option value="all">{t('admin.all')}</option>
                </select>
              </FilterField>
            </div>
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t('admin.users.name')}</TableHead>
                <TableHead>{t('admin.users.level')}</TableHead>
                <TableHead>{t('admin.users.groups')}</TableHead>
                <TableHead>{t('admin.users.login')}</TableHead>
                <TableHead>{t('admin.users.createdCol')}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <Empty cols={5} message="…" />
              ) : shownUsers.length === 0 ? (
                <Empty cols={5} message={t('admin.users.empty')} />
              ) : (
                shownUsers.map((u) => (
                  <TableRow key={u.id} className="cursor-pointer" onClick={() => setParam('user', u.name)}>
                    <TableCell>
                      <p className="font-medium">{u.name}{u.name === session?.name && <span className="ml-2 text-xs font-normal text-muted-foreground">({t('admin.users.you')})</span>}</p>
                      {u.email && <p className="text-xs text-muted-foreground">{u.email}</p>}
                    </TableCell>
                    <TableCell><LevelBadge level={u.access_level} /></TableCell>
                    <TableCell>
                      {u.groups.length === 0 ? <span className="text-xs text-muted-foreground">—</span> : (
                        <span className="flex flex-wrap gap-1">{u.groups.map((g) => <Badge key={g} variant="outline">{groups?.find((x) => x.slug === g)?.name ?? g}</Badge>)}</span>
                      )}
                    </TableCell>
                    <TableCell>
                      {u.revoked_at ? <Badge variant="outline">{t('admin.users.inactive')}</Badge> : u.has_password ? <Badge variant="success">{t('admin.users.passwordOn')}</Badge> : <span className="text-xs text-muted-foreground">{t('admin.users.tokenOnly')}</span>}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">{date(u.created_at)}</TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      ) : (
        <div className="rounded-2xl border bg-card p-4">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t('admin.groups.name')}</TableHead>
                <TableHead>{t('admin.groups.members')}</TableHead>
                <TableHead>{t('admin.groups.grants')}</TableHead>
                <TableHead>{t('admin.users.createdCol')}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <Empty cols={4} message="…" />
              ) : groups.length === 0 ? (
                <Empty cols={4} message={t('admin.groups.empty')} />
              ) : (
                groups.map((g) => (
                  <TableRow key={g.id} className="cursor-pointer" onClick={() => setParam('group', g.slug)}>
                    <TableCell>
                      <p className="font-medium">{g.name}</p>
                      <p className="font-mono text-xs text-muted-foreground">{g.slug}</p>
                    </TableCell>
                    <TableCell className="text-sm">{t('admin.groups.memberCount', { count: g.members.length })}</TableCell>
                    <TableCell>
                      <ModuleChips
                        modules={g.allowed_modules}
                        all={t('admin.groups.noScopesShort')}
                        restricted={Object.fromEntries(MODULES.map((m) => [m, Array.isArray(g.module_scopes[m]?.[RESTRICTION_KEY[m]])]))}
                      />
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">{date(g.created_at)}</TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
          <p className="mt-3 text-xs text-muted-foreground">{t('admin.groups.legend')}</p>
        </div>
      )}

      <UserDetail user={selectedUser} groups={groups ?? []} me={session?.name} onClose={() => setParam('user', null)} onChanged={reload} />
      <GroupDetail group={selectedGroup} users={users ?? []} onClose={() => setParam('group', null)} onChanged={reload} />
      <NewUserPanel open={newUser} onClose={() => setNewUser(false)} onCreated={(u) => { setNewUser(false); reload(); setParam('user', u.name); }} />
      <NewGroupPanel open={newGroup} onClose={() => setNewGroup(false)} onCreated={(g) => { setNewGroup(false); reload(); setParam('group', g.slug); }} />
    </>
  );
}
