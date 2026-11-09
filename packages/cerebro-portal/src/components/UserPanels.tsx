import { useEffect, useState, type FormEvent } from 'react';
import { Badge, Button, ConfirmDialog } from '@alquimias/ixchel';
import { Plus, UserCheck, UserX, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  MIN_PASSWORD_LENGTH,
  addGroupMember,
  createUser,
  removeGroupMember,
  setUserPassword,
  updateUser,
  type AccessLevel,
  type AdminUser,
  type Group,
} from '../lib/admin';
import { LevelBadge, PanelHeader, useAdminError } from './adminUi';
import { Field, INPUT } from './formUi';
import { useDateTime } from './memoryUi';
import { ResizableSheet } from './ResizableSheet';
import { SELECT } from './flowUi';

const LEVELS: AccessLevel[] = ['user', 'owner', 'admin'];

export function NewUserPanel({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (u: AdminUser) => void }) {
  const { t } = useTranslation();
  const errMsg = useAdminError();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [level, setLevel] = useState<AccessLevel>('user');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  useEffect(() => {
    if (!open) {
      setName('');
      setEmail('');
      setLevel('user');
      setPassword('');
      setError(undefined);
    }
  }, [open]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      onCreated(await createUser({ name: name.trim(), email: email.trim() || undefined, access_level: level, password: password || undefined }));
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };
  const passwordOk = password === '' || password.length >= MIN_PASSWORD_LENGTH;

  return (
    <ResizableSheet open={open} onClose={onClose}>
      <PanelHeader title={t('admin.users.newTitle')} description={t('admin.users.newDesc')} />
      <form id="new-user-form" onSubmit={(e) => void submit(e)} className="flex-1 space-y-4 overflow-y-auto [scrollbar-gutter:stable] p-6">
        <Field label={t('admin.users.name')} hint={t('admin.users.nameHint')}>
          <input autoFocus required maxLength={100} className={INPUT} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label={t('admin.users.email')}>
          <input type="email" className={INPUT} value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label={t('admin.users.level')} hint={t(`admin.levelHint.${level}`)}>
          <select className={SELECT} value={level} onChange={(e) => setLevel(e.target.value as AccessLevel)}>
            {LEVELS.map((l) => <option key={l} value={l}>{t(`admin.level.${l}`)}</option>)}
          </select>
        </Field>
        <Field label={t('admin.users.password')} hint={t('admin.users.passwordHint', { min: MIN_PASSWORD_LENGTH })}>
          <input type="password" autoComplete="new-password" className={INPUT} value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        {!passwordOk && <p className="text-xs text-destructive-text">{t('admin.users.passwordShort', { min: MIN_PASSWORD_LENGTH })}</p>}
        {error && <p role="alert" className="text-sm text-destructive-text">{error}</p>}
      </form>
      <div className="flex justify-end gap-2 border-t p-3">
        <Button type="button" variant="ghost" disabled={busy} onClick={onClose}>{t('admin.cancel')}</Button>
        <Button type="submit" form="new-user-form" variant="primary" arrow={false} disabled={busy || !name.trim() || !passwordOk}>
          {busy ? t('admin.creating') : t('admin.create')}
        </Button>
      </div>
    </ResizableSheet>
  );
}

/** One user: edit email and level, manage group membership, reset the password, deactivate. */
export function UserDetail({ user, groups, me, onClose, onChanged }: {
  user: AdminUser | null;
  groups: Group[];
  me: string | undefined;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const fmt = useDateTime();
  const errMsg = useAdminError();
  const [email, setEmail] = useState('');
  const [level, setLevel] = useState<AccessLevel>('user');
  const [password, setPassword] = useState('');
  const [addTo, setAddTo] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [confirm, setConfirm] = useState<'deactivate' | null>(null);

  useEffect(() => {
    setEmail(user?.email ?? '');
    setLevel(user?.access_level ?? 'user');
    setPassword('');
    setAddTo('');
    setError(undefined);
    setNotice(undefined);
  }, [user?.id, user?.email, user?.access_level]); // eslint-disable-line react-hooks/exhaustive-deps

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

  if (!user) return <ResizableSheet open={false} onClose={onClose}>{null}</ResizableSheet>;
  const inactive = user.revoked_at !== null;
  const dirty = email.trim() !== (user.email ?? '') || level !== user.access_level;
  const memberOf = groups.filter((g) => user.groups.includes(g.slug));
  const available = groups.filter((g) => !user.groups.includes(g.slug));
  const passwordOk = password.length >= MIN_PASSWORD_LENGTH;

  return (
    <ResizableSheet open onClose={onClose}>
      <PanelHeader title={user.name} description={user.email ?? undefined} />
      <div className="flex-1 space-y-6 overflow-y-auto [scrollbar-gutter:stable] p-6">
        <div className="flex flex-wrap items-center gap-2">
          <LevelBadge level={user.access_level} />
          <Badge variant={inactive ? 'outline' : 'success'}>{inactive ? t('admin.users.inactive') : t('admin.users.active')}</Badge>
          {user.name === me && <Badge variant="info">{t('admin.users.you')}</Badge>}
          <span className="text-xs text-muted-foreground">{t('admin.users.created', { date: fmt(user.created_at) })}</span>
        </div>

        <section className="space-y-3">
          <h3 className="text-xs font-bold tracking-wider text-muted-foreground uppercase">{t('admin.users.profile')}</h3>
          <Field label={t('admin.users.email')}>
            <input type="email" className={INPUT} value={email} onChange={(e) => setEmail(e.target.value)} />
          </Field>
          <Field label={t('admin.users.level')} hint={t(`admin.levelHint.${level}`)}>
            <select className={SELECT} value={level} onChange={(e) => setLevel(e.target.value as AccessLevel)}>
              {LEVELS.map((l) => <option key={l} value={l}>{t(`admin.level.${l}`)}</option>)}
            </select>
          </Field>
          <div className="flex justify-end">
            <Button size="sm" variant="primary" arrow={false} disabled={busy || !dirty} onClick={() => void run(() => updateUser(user.name, { email: email.trim(), access_level: level }), t('admin.saved'))}>
              {t('admin.save')}
            </Button>
          </div>
        </section>

        <section className="space-y-3">
          <h3 className="text-xs font-bold tracking-wider text-muted-foreground uppercase">{t('admin.users.groups')}</h3>
          {memberOf.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t('admin.users.noGroups')}</p>
          ) : (
            <ul className="flex flex-wrap gap-2">
              {memberOf.map((g) => (
                <li key={g.slug} className="inline-flex items-center gap-1 rounded-full border py-1 pr-1 pl-3 text-sm">
                  {g.name}
                  <button type="button" aria-label={t('admin.users.removeFrom', { group: g.name })} disabled={busy} onClick={() => void run(() => removeGroupMember(g.slug, user.name))} className="rounded-full p-1 hover:bg-muted">
                    <X className="size-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          )}
          {available.length > 0 && (
            <div className="flex gap-2">
              <select className={SELECT} value={addTo} onChange={(e) => setAddTo(e.target.value)}>
                <option value="">{t('admin.users.addToGroup')}</option>
                {available.map((g) => <option key={g.slug} value={g.slug}>{g.name}</option>)}
              </select>
              <Button size="sm" variant="tertiary" leadingIcon={Plus} disabled={busy || !addTo} onClick={() => void run(async () => { await addGroupMember(addTo, user.name); setAddTo(''); })}>
                {t('admin.users.add')}
              </Button>
            </div>
          )}
        </section>

        <section className="space-y-3">
          <h3 className="text-xs font-bold tracking-wider text-muted-foreground uppercase">{t('admin.users.password')}</h3>
          <p className="text-xs text-muted-foreground">{user.has_password ? t('admin.users.hasPassword') : t('admin.users.noPassword')}</p>
          <Field label={t('admin.users.newPassword')} hint={t('admin.users.resetHint', { min: MIN_PASSWORD_LENGTH })}>
            <input type="password" autoComplete="new-password" className={INPUT} value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          <div className="flex justify-end">
            <Button size="sm" variant="tertiary" disabled={busy || !passwordOk} onClick={() => void run(async () => { await setUserPassword(user.name, password); setPassword(''); }, t('admin.users.passwordSet'))}>
              {user.has_password ? t('admin.users.resetPassword') : t('admin.users.setPassword')}
            </Button>
          </div>
        </section>

        {notice && <p role="status" className="text-sm text-emerald-600 dark:text-emerald-400">{notice}</p>}
        {error && <p role="alert" className="text-sm text-destructive-text">{error}</p>}
      </div>
      <div className="flex items-center justify-between border-t p-3">
        {inactive ? (
          <Button variant="tertiary" leadingIcon={UserCheck} disabled={busy} onClick={() => void run(() => updateUser(user.name, { active: true }))}>{t('admin.users.reactivate')}</Button>
        ) : (
          <Button variant="ghost" className="text-destructive-text" leadingIcon={UserX} disabled={busy} onClick={() => setConfirm('deactivate')}>{t('admin.users.deactivate')}</Button>
        )}
        <Button variant="ghost" onClick={onClose}>{t('admin.close')}</Button>
      </div>
      <ConfirmDialog
        open={confirm === 'deactivate'}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={t('admin.users.deactivateTitle', { name: user.name })}
        description={t('admin.users.deactivateDesc')}
        confirmLabel={t('admin.users.deactivate')}
        cancelLabel={t('admin.cancel')}
        isPending={busy}
        onConfirm={() => { setConfirm(null); void run(() => updateUser(user.name, { active: false })); }}
      />
    </ResizableSheet>
  );
}
