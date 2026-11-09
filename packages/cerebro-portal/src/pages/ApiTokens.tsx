import { useCallback, useEffect, useMemo, useState } from 'react';
import { Plus, Search, Trash2 } from 'lucide-react';
import { Badge, Button, ConfirmDialog, PageHeader, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@alquimias/ixchel';
import { useTranslation } from 'react-i18next';
import { LevelBadge, ModuleChips, useAdminError } from '../components/adminUi';
import { FilterField, SELECT } from '../components/flowUi';
import { useDateTime } from '../components/memoryUi';
import { NewTokenPanel } from '../components/TokenPanels';
import { MODULES, RESTRICTION_KEY, isSessionToken, listTokens, listUsers, revokeToken, type AdminUser, type ApiToken } from '../lib/admin';

export function ApiTokens() {
  const { t } = useTranslation();
  const dt = useDateTime();
  const errMsg = useAdminError();
  const [tokens, setTokens] = useState<ApiToken[] | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [error, setError] = useState<string>();
  const [q, setQ] = useState('');
  const [status, setStatus] = useState<'active' | 'revoked' | 'all'>('active');
  const [sessions, setSessions] = useState(false);
  const [creating, setCreating] = useState(false);
  const [toRevoke, setToRevoke] = useState<ApiToken | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => {
    Promise.all([listTokens(), listUsers()]).then(
      ([tk, us]) => {
        setTokens(tk);
        setUsers(us);
        setError(undefined);
      },
      () => setError(t('admin.loadError')),
    );
  }, [t]);
  useEffect(reload, [reload]);

  const owner = (tk: ApiToken) => (tk.user_id ? users.find((u) => u.id === tk.user_id) ?? null : null);
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (tokens ?? [])
      .filter((tk) => (sessions || !isSessionToken(tk)) && (status === 'all' || (status === 'active') === (tk.revoked_at === null)))
      .filter((tk) => !needle || tk.name.toLowerCase().includes(needle) || (owner(tk)?.name ?? '').toLowerCase().includes(needle))
      .sort((a, b) => b.created_at.localeCompare(a.created_at));
  }, [tokens, users, q, status, sessions]); // eslint-disable-line react-hooks/exhaustive-deps

  const doRevoke = async () => {
    if (!toRevoke) return;
    setBusy(true);
    try {
      await revokeToken(toRevoke.name);
      setToRevoke(null);
      reload();
    } catch (e) {
      setError(errMsg(e));
      setToRevoke(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader title={t('admin.tokens.title')} description={t('admin.tokens.description')} />
      <div className="flex justify-end">
        <Button variant="primary" leadingIcon={Plus} onClick={() => setCreating(true)}>{t('admin.tokens.new')}</Button>
      </div>
      {error && <p role="alert" className="text-sm text-destructive-text">{error}</p>}
      <div className="space-y-4 rounded-2xl border bg-card p-4">
        <div className="flex flex-wrap items-end gap-3">
          <label className="relative block min-w-56 flex-1">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
            <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('admin.tokens.search')} className="h-9 w-full rounded-lg border bg-background pr-3 pl-9 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring" />
          </label>
          <div className="w-44">
            <FilterField label={t('admin.status')}>
              <select className={SELECT} value={status} onChange={(e) => setStatus(e.target.value as typeof status)}>
                <option value="active">{t('admin.tokens.active')}</option>
                <option value="revoked">{t('admin.tokens.revoked')}</option>
                <option value="all">{t('admin.all')}</option>
              </select>
            </FilterField>
          </div>
          <label className="flex h-9 items-center gap-2 text-sm">
            <input type="checkbox" checked={sessions} onChange={(e) => setSessions(e.target.checked)} />
            {t('admin.tokens.showSessions')}
          </label>
        </div>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t('admin.tokens.name')}</TableHead>
              <TableHead>{t('admin.tokens.owner')}</TableHead>
              <TableHead>{t('admin.tokens.permissions')}</TableHead>
              <TableHead>{t('admin.tokens.modules')}</TableHead>
              <TableHead>{t('admin.tokens.createdCol')}</TableHead>
              <TableHead className="w-24" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {tokens === null ? (
              <TableRow><TableCell colSpan={6} className="py-10 text-center text-sm text-muted-foreground">…</TableCell></TableRow>
            ) : shown.length === 0 ? (
              <TableRow><TableCell colSpan={6} className="py-10 text-center text-sm text-muted-foreground">{t('admin.tokens.empty')}</TableCell></TableRow>
            ) : (
              shown.map((tk) => {
                const o = owner(tk);
                return (
                  <TableRow key={tk.id}>
                    <TableCell>
                      <p className="font-medium break-all">{tk.name}</p>
                      {tk.revoked_at && <Badge variant="outline">{t('admin.tokens.revokedOn', { date: dt(tk.revoked_at) })}</Badge>}
                      {!tk.revoked_at && tk.expires_at && <p className="text-xs text-muted-foreground">{t('admin.tokens.expires', { date: dt(tk.expires_at) })}</p>}
                    </TableCell>
                    <TableCell>
                      {tk.user_id ? (
                        <span className="flex flex-wrap items-center gap-1.5 text-sm">{o?.name ?? t('admin.tokens.unknownUser')}{o && <LevelBadge level={o.access_level} />}</span>
                      ) : (
                        <span className="flex flex-wrap items-center gap-1.5 text-sm">{t('admin.tokens.service')}<LevelBadge level={tk.access_level} /></span>
                      )}
                    </TableCell>
                    <TableCell><span className="flex gap-1">{tk.scopes.map((s) => <Badge key={s} variant="outline">{s}</Badge>)}</span></TableCell>
                    <TableCell>
                      <ModuleChips modules={tk.allowed_modules} restricted={Object.fromEntries(MODULES.map((m) => [m, Array.isArray(tk.module_scopes[m]?.[RESTRICTION_KEY[m]])]))} />
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">{dt(tk.created_at)}</TableCell>
                    <TableCell className="text-right">
                      {!tk.revoked_at && (
                        <Button size="sm" variant="ghost" className="text-destructive-text" leadingIcon={Trash2} onClick={() => setToRevoke(tk)}>{t('admin.tokens.revoke')}</Button>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
        <p className="text-xs text-muted-foreground">{t('admin.tokens.legend')}</p>
      </div>
      <NewTokenPanel open={creating} users={users} onClose={() => setCreating(false)} onCreated={reload} />
      <ConfirmDialog
        open={toRevoke !== null}
        onOpenChange={(o) => !o && setToRevoke(null)}
        title={t('admin.tokens.revokeTitle', { name: toRevoke?.name })}
        description={t('admin.tokens.revokeDesc')}
        confirmLabel={t('admin.tokens.revoke')}
        cancelLabel={t('admin.cancel')}
        isPending={busy}
        onConfirm={() => void doRevoke()}
      />
    </>
  );
}
