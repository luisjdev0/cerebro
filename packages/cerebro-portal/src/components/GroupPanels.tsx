import { useEffect, useState, type FormEvent } from 'react';
import { Button, ConfirmDialog } from '@alquimias/ixchel';
import { Plus, Trash2, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { addGroupMember, createGroup, deleteGroup, removeGroupMember, setGroupScopes, updateGroup, type AdminUser, type Group } from '../lib/admin';
import { slugify } from '../lib/memories';
import { PanelHeader, useAdminError } from './adminUi';
import { SELECT } from './flowUi';
import { Field, INPUT } from './formUi';
import { ResizableSheet } from './ResizableSheet';
import { ScopeEditor, emptyScope, hasEmptyRestriction, scopeFrom, scopesPayload, type ScopeValue } from './ScopeEditor';

export function NewGroupPanel({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (g: Group) => void }) {
  const { t } = useTranslation();
  const errMsg = useAdminError();
  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  useEffect(() => {
    if (!open) {
      setName('');
      setSlug('');
      setTouched(false);
      setError(undefined);
    }
  }, [open]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      onCreated(await createGroup({ slug, name: name.trim() }));
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <ResizableSheet open={open} onClose={onClose}>
      <PanelHeader title={t('admin.groups.newTitle')} description={t('admin.groups.newDesc')} />
      <form id="new-group-form" onSubmit={(e) => void submit(e)} className="flex-1 space-y-4 overflow-y-auto p-6">
        <Field label={t('admin.groups.name')}>
          <input autoFocus required className={INPUT} value={name} onChange={(e) => { setName(e.target.value); if (!touched) setSlug(slugify(e.target.value)); }} />
        </Field>
        <Field label={t('admin.groups.slug')} hint={t('admin.groups.slugHint')}>
          <input required pattern="[a-z0-9]+(-[a-z0-9]+)*" maxLength={100} className={`${INPUT} font-mono`} value={slug} onChange={(e) => { setTouched(true); setSlug(e.target.value); }} />
        </Field>
        {error && <p role="alert" className="text-sm text-destructive-text">{error}</p>}
      </form>
      <div className="flex justify-end gap-2 border-t p-3">
        <Button type="button" variant="ghost" disabled={busy} onClick={onClose}>{t('admin.cancel')}</Button>
        <Button type="submit" form="new-group-form" variant="primary" arrow={false} disabled={busy || !name.trim() || !slug}>
          {busy ? t('admin.creating') : t('admin.create')}
        </Button>
      </div>
    </ResizableSheet>
  );
}

/** One group: rename, members, the modules/scopes it grants to its members, delete. */
export function GroupDetail({ group, users, onClose, onChanged }: {
  group: Group | null;
  users: AdminUser[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const errMsg = useAdminError();
  const [name, setName] = useState('');
  const [scope, setScope] = useState<ScopeValue>(emptyScope());
  const [addUser, setAddUser] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    setName(group?.name ?? '');
    setScope(group ? scopeFrom(group.allowed_modules, group.module_scopes) : emptyScope());
    setAddUser('');
    setError(undefined);
    setNotice(undefined);
  }, [group?.slug]); // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (fn: () => Promise<unknown>, ok?: string) => {
    setBusy(true);
    setError(undefined);
    setNotice(undefined);
    try {
      await fn();
      if (ok) setNotice(ok);
      onChanged();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  if (!group) return <ResizableSheet open={false} onClose={onClose}>{null}</ResizableSheet>;
  const candidates = users.filter((u) => !group.members.includes(u.name) && u.revoked_at === null);
  const scopeInvalid = scope.modules.length === 0 || hasEmptyRestriction(scope);

  return (
    <ResizableSheet open onClose={onClose}>
      <PanelHeader title={group.name} description={group.slug} />
      <div className="flex-1 space-y-6 overflow-y-auto p-6">
        <section className="space-y-3">
          <h3 className="text-xs font-bold tracking-wider text-muted-foreground uppercase">{t('admin.groups.details')}</h3>
          <Field label={t('admin.groups.name')}>
            <input className={INPUT} value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <div className="flex justify-end">
            <Button size="sm" variant="primary" arrow={false} disabled={busy || !name.trim() || name.trim() === group.name} onClick={() => void run(() => updateGroup(group.slug, name.trim()), t('admin.saved'))}>
              {t('admin.save')}
            </Button>
          </div>
        </section>

        <section className="space-y-3">
          <h3 className="text-xs font-bold tracking-wider text-muted-foreground uppercase">{t('admin.groups.members')}</h3>
          {group.members.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t('admin.groups.noMembers')}</p>
          ) : (
            <ul className="flex flex-wrap gap-2">
              {group.members.map((m) => (
                <li key={m} className="inline-flex items-center gap-1 rounded-full border py-1 pr-1 pl-3 text-sm">
                  {m}
                  <button type="button" aria-label={t('admin.groups.removeMember', { user: m })} disabled={busy} onClick={() => void run(() => removeGroupMember(group.slug, m))} className="rounded-full p-1 hover:bg-muted">
                    <X className="size-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          )}
          {candidates.length > 0 && (
            <div className="flex gap-2">
              <select className={SELECT} value={addUser} onChange={(e) => setAddUser(e.target.value)}>
                <option value="">{t('admin.groups.addMember')}</option>
                {candidates.map((u) => <option key={u.id} value={u.name}>{u.name}</option>)}
              </select>
              <Button size="sm" variant="tertiary" leadingIcon={Plus} disabled={busy || !addUser} onClick={() => void run(async () => { await addGroupMember(group.slug, addUser); setAddUser(''); })}>
                {t('admin.users.add')}
              </Button>
            </div>
          )}
        </section>

        <section className="space-y-3">
          <h3 className="text-xs font-bold tracking-wider text-muted-foreground uppercase">{t('admin.groups.scopes')}</h3>
          <p className="text-xs text-muted-foreground">{t('admin.groups.scopesHint')}</p>
          <ScopeEditor value={scope} onChange={setScope} disabled={busy} />
          {group.allowed_modules === null && <p className="text-xs text-amber-600 dark:text-amber-400">{t('admin.groups.noScopesYet')}</p>}
          {scopeInvalid && <p className="text-xs text-muted-foreground">{t('admin.groups.scopeInvalid')}</p>}
          <div className="flex justify-end">
            <Button size="sm" variant="primary" arrow={false} disabled={busy || scopeInvalid} onClick={() => void run(() => setGroupScopes(group.slug, scope.modules, scopesPayload(scope)), t('admin.saved'))}>
              {t('admin.groups.saveScopes')}
            </Button>
          </div>
        </section>

        {notice && <p role="status" className="text-sm text-emerald-600 dark:text-emerald-400">{notice}</p>}
        {error && <p role="alert" className="text-sm text-destructive-text">{error}</p>}
      </div>
      <div className="flex items-center justify-between border-t p-3">
        <Button variant="ghost" className="text-destructive-text" leadingIcon={Trash2} disabled={busy} onClick={() => setConfirmDelete(true)}>{t('admin.groups.delete')}</Button>
        <Button variant="ghost" onClick={onClose}>{t('admin.close')}</Button>
      </div>
      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={t('admin.groups.deleteTitle', { name: group.name })}
        description={t('admin.groups.deleteDesc', { count: group.members.length })}
        confirmLabel={t('admin.groups.delete')}
        cancelLabel={t('admin.cancel')}
        isPending={busy}
        onConfirm={() => { setConfirmDelete(false); void run(async () => { await deleteGroup(group.slug); onClose(); }); }}
      />
    </ResizableSheet>
  );
}
