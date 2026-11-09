import { useCallback, useEffect, useMemo, useState } from 'react';
import { Plus, Search, Trash2 } from 'lucide-react';
import { Badge, Button, ConfirmDialog, PageHeader, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@alquimias/ixchel';
import { useTranslation } from 'react-i18next';
import { LevelBadge, ModuleChips, ToggleSwitch, useAdminError } from '../components/adminUi';
import { FilterField, SELECT } from '../components/flowUi';
import { useDateTime } from '../components/memoryUi';
import { NewTokenPanel } from '../components/TokenPanels';
import { MODULES, RESTRICTION_KEY, isSessionToken, listTokens, listUsers, revokeToken, setTokenActive, type AdminUser, type ApiToken } from '../lib/admin';

const tokenState = (tk: ApiToken): 'on' | 'off' | 'revoked' => (tk.revoked_at ? 'revoked' : tk.disabled_at ? 'off' : 'on');

export function ApiTokens() {
  const { t } = useTranslation();
  const dt = useDateTime();
  const errMsg = useAdminError();
  const [tokens, setTokens] = useState<ApiToken[] | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [error, setError] = useState<string>();
  const [q, setQ] = useState('');
  const [status, setStatus] = useState<'on' | 'off' | 'revoked' | 'all'>('all');
  const [sessions, setSessions] = useState(false);
  const [creating, setCreating] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [toRevoke, setToRevoke] = useState<ApiToken | null>(null);

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
      .filter((tk) => (sessions || !isSessionToken(tk)) && (status === 'all' || tokenState(tk) === status))
      .filter((tk) => !needle || tk.name.toLowerCase().includes(needle) || (owner(tk)?.name ?? '').toLowerCase().includes(needle))
      .sort((a, b) => b.created_at.localeCompare(a.created_at));
  }, [tokens, users, q, status, sessions]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = async (tk: ApiToken, active: boolean) => {
    setBusyId(tk.id);
    setError(undefined);
    try {
      const updated = await setTokenActive(tk.id, active);
      setTokens((list) => (list ? list.map((x) => (x.id === updated.id ? updated : x)) : list));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusyId(null);
    }
  };

  const doRevoke = async () => {
    if (!toRevoke) return;
    setBusyId(toRevoke.id);
    try {
      const updated = await revokeToken(toRevoke.name);
      setTokens((list) => (list ? list.map((x) => (x.id === updated.id ? { ...x, ...updated } : x)) : list));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusyId(null);
      setToRevoke(null);
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
                <option value="all">{t('admin.all')}</option>
                <option value="on">{t('admin.tokens.statusOnPlural')}</option>
                <option value="off">{t('admin.tokens.statusOffPlural')}</option>
                <option value="revoked">{t('admin.tokens.statusRevokedPlural')}</option>
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
              <TableHead>{t('admin.status')}</TableHead>
              <TableHead className="w-28" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {tokens === null ? (
              <TableRow><TableCell colSpan={7} className="py-10 text-center text-sm text-muted-foreground">…</TableCell></TableRow>
            ) : shown.length === 0 ? (
              <TableRow><TableCell colSpan={7} className="py-10 text-center text-sm text-muted-foreground">{t('admin.tokens.empty')}</TableCell></TableRow>
            ) : (
              shown.map((tk) => {
                const o = owner(tk);
                return (
                  <TableRow key={tk.id}>
                    <TableCell>
                      <p className="font-medium break-all">{tk.name}</p>
                      {tk.revoked_at ? <p className="text-xs text-muted-foreground">{t('admin.tokens.revokedOn', { date: dt(tk.revoked_at) })}</p> : tk.disabled_at && <p className="text-xs text-muted-foreground">{t('admin.tokens.offSince', { date: dt(tk.disabled_at) })}</p>}
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
                    <TableCell>
                      {tk.revoked_at ? (
                        <Badge variant="outline">{t('admin.tokens.statusRevoked')}</Badge>
                      ) : (
                        <span className="flex items-center gap-2">
                          <ToggleSwitch
                            checked={tk.disabled_at === null}
                            disabled={busyId === tk.id}
                            label={tk.disabled_at === null ? t('admin.tokens.turnOff', { name: tk.name }) : t('admin.tokens.turnOn', { name: tk.name })}
                            onChange={(next) => void toggle(tk, next)}
                          />
                          <span className="text-xs text-muted-foreground">{tk.disabled_at === null ? t('admin.tokens.statusOn') : t('admin.tokens.statusOff')}</span>
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      {!tk.revoked_at && (
                        <Button size="sm" variant="ghost" className="text-destructive-text" leadingIcon={Trash2} disabled={busyId === tk.id} onClick={() => setToRevoke(tk)}>{t('admin.tokens.revoke')}</Button>
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
      <ConfirmDialog
        open={toRevoke !== null}
        onOpenChange={(o) => !o && setToRevoke(null)}
        title={t('admin.tokens.revokeTitle', { name: toRevoke?.name })}
        description={t('admin.tokens.revokeDesc')}
        confirmLabel={t('admin.tokens.revoke')}
        cancelLabel={t('admin.cancel')}
        isPending={busyId !== null}
        onConfirm={() => void doRevoke()}
      />
      <NewTokenPanel open={creating} users={users} onClose={() => setCreating(false)} onCreated={reload} />
    </>
  );
}
