import { useEffect, useState, type FormEvent } from 'react';
import { Button } from '@alquimias/ixchel';
import { KeyRound } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { createToken, type AccessLevel, type AdminUser } from '../lib/admin';
import { PanelHeader, useAdminError } from './adminUi';
import { SELECT } from './flowUi';
import { Field, INPUT } from './formUi';
import { CopyButton } from './MarkdownView';
import { ResizableSheet } from './ResizableSheet';
import { ScopeEditor, emptyScope, hasEmptyRestriction, scopesPayload, type ScopeValue } from './ScopeEditor';

type Kind = 'user' | 'service';
const LEVELS: AccessLevel[] = ['user', 'owner', 'admin'];

/** Creates a token and shows its secret exactly once: the API stores only a hash. */
export function NewTokenPanel({ open, users, onClose, onCreated }: { open: boolean; users: AdminUser[]; onClose: () => void; onCreated: () => void }) {
  const { t } = useTranslation();
  const errMsg = useAdminError();
  const [kind, setKind] = useState<Kind>('user');
  const [name, setName] = useState('');
  const [user, setUser] = useState('');
  const [level, setLevel] = useState<AccessLevel>('user');
  const [read, setRead] = useState(true);
  const [write, setWrite] = useState(false);
  const [scope, setScope] = useState<ScopeValue>(emptyScope());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [secret, setSecret] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setKind('user');
      setName('');
      setUser('');
      setLevel('user');
      setRead(true);
      setWrite(false);
      setScope(emptyScope());
      setError(undefined);
      setSecret(null); // the secret never survives closing the panel
    }
  }, [open]);

  const activeUsers = users.filter((u) => u.revoked_at === null);
  const scopes = [...(read ? ['read' as const] : []), ...(write ? ['write' as const] : [])];
  const needsModules = kind === 'service';
  const valid =
    name.trim() !== '' && scopes.length > 0 && !hasEmptyRestriction(scope) && (kind === 'user' ? user !== '' : scope.modules.length > 0);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      const withModules = scope.modules.length > 0;
      const created = await createToken({
        name: name.trim(),
        scopes,
        ...(kind === 'user' ? { user } : { access_level: level }),
        ...(withModules ? { allowed_modules: scope.modules, module_scopes: scopesPayload(scope) } : {}),
      });
      setSecret(created.token);
      onCreated();
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  if (secret) {
    return (
      <ResizableSheet open={open} onClose={onClose}>
        <PanelHeader title={t('admin.tokens.createdTitle')} description={t('admin.tokens.createdDesc')} />
        <div className="flex-1 space-y-4 overflow-y-auto [scrollbar-gutter:stable] p-6">
          <div className="flex items-center gap-2 rounded-xl border bg-muted/40 p-3">
            <code className="min-w-0 flex-1 text-sm break-all select-all">{secret}</code>
            <CopyButton text={secret} />
          </div>
          <p role="alert" className="rounded-lg bg-amber-500/15 p-3 text-sm text-amber-700 dark:text-amber-300">{t('admin.tokens.once')}</p>
        </div>
        <div className="flex justify-end border-t p-3">
          <Button variant="primary" arrow={false} onClick={onClose}>{t('admin.tokens.savedIt')}</Button>
        </div>
      </ResizableSheet>
    );
  }

  return (
    <ResizableSheet open={open} onClose={onClose}>
      <PanelHeader title={t('admin.tokens.newTitle')} description={t('admin.tokens.newDesc')} />
      <form id="new-token-form" onSubmit={(e) => void submit(e)} className="flex-1 space-y-5 overflow-y-auto [scrollbar-gutter:stable] p-6">
        <Field label={t('admin.tokens.name')} hint={t('admin.tokens.nameHint')}>
          <input autoFocus required maxLength={100} className={INPUT} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>

        <fieldset className="space-y-2">
          <legend className="text-xs font-semibold">{t('admin.tokens.kind')}</legend>
          {(['user', 'service'] as Kind[]).map((k) => (
            <label key={k} className="flex items-start gap-2 text-sm">
              <input type="radio" name="kind" className="mt-1" checked={kind === k} onChange={() => setKind(k)} />
              <span>
                <span className="font-medium">{t(`admin.tokens.kinds.${k}`)}</span>
                <span className="block text-xs text-muted-foreground">{t(`admin.tokens.kinds.${k}Hint`)}</span>
              </span>
            </label>
          ))}
        </fieldset>

        {kind === 'user' ? (
          <Field label={t('admin.tokens.user')}>
            <select required className={SELECT} value={user} onChange={(e) => setUser(e.target.value)}>
              <option value="">{t('admin.tokens.chooseUser')}</option>
              {activeUsers.map((u) => <option key={u.id} value={u.name}>{u.name} · {t(`admin.level.${u.access_level}`)}</option>)}
            </select>
          </Field>
        ) : (
          <Field label={t('admin.users.level')} hint={t(`admin.levelHint.${level}`)}>
            <select className={SELECT} value={level} onChange={(e) => setLevel(e.target.value as AccessLevel)}>
              {LEVELS.map((l) => <option key={l} value={l}>{t(`admin.level.${l}`)}</option>)}
            </select>
          </Field>
        )}

        <fieldset className="space-y-1.5">
          <legend className="text-xs font-semibold">{t('admin.tokens.permissions')}</legend>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={read} onChange={(e) => setRead(e.target.checked)} />{t('admin.tokens.read')}</label>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={write} onChange={(e) => setWrite(e.target.checked)} />{t('admin.tokens.write')}</label>
          <p className="text-[11px] text-muted-foreground">{t('admin.tokens.permissionsHint')}</p>
        </fieldset>

        <section className="space-y-2">
          <h3 className="text-xs font-semibold">{t('admin.tokens.modules')}</h3>
          <p className="text-xs text-muted-foreground">{needsModules ? t('admin.tokens.modulesRequired') : t('admin.tokens.modulesOptional')}</p>
          <ScopeEditor value={scope} onChange={setScope} disabled={busy} />
        </section>

        {error && <p role="alert" className="text-sm text-destructive-text">{error}</p>}
      </form>
      <div className="flex justify-end gap-2 border-t p-3">
        <Button type="button" variant="ghost" disabled={busy} onClick={onClose}>{t('admin.cancel')}</Button>
        <Button type="submit" form="new-token-form" variant="primary" arrow={false} leadingIcon={KeyRound} disabled={busy || !valid}>
          {busy ? t('admin.creating') : t('admin.tokens.generate')}
        </Button>
      </div>
    </ResizableSheet>
  );
}
