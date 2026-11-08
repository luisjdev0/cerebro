import { useState, type FormEvent } from 'react';
import { Button, ConfirmDialog, Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@alquimias/ixchel';
import { Pencil, Trash2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { CONTEXT_KINDS, countContextMemories, createContext, createMemory, deleteContext, updateContext, MEMORY_TYPES, slugify, type Context, type Memory, type MemoryType } from '../lib/memories';
import { Field, INPUT, useErrorMessage } from './formUi';

export function NewContextDialog({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (c: Context) => void }) {
  const { t } = useTranslation();
  const errMsg = useErrorMessage();
  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  const [slugTouched, setSlugTouched] = useState(false);
  const [kind, setKind] = useState<string>('project');
  const [description, setDescription] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      const c = await createContext({ slug, name: name.trim(), kind, description: description.trim() || undefined });
      onCreated(c);
      setName('');
      setSlug('');
      setSlugTouched(false);
      setDescription('');
      onClose();
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && !busy && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('memory.form.newContextTitle')}</DialogTitle>
          <DialogDescription>{t('memory.form.newContextDesc')}</DialogDescription>
        </DialogHeader>
        <form onSubmit={(e) => void submit(e)} className="space-y-4">
          <Field label={t('memory.form.name')}>
            <input
              autoFocus
              required
              className={INPUT}
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                if (!slugTouched) setSlug(slugify(e.target.value));
              }}
            />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t('memory.form.slug')} hint={t('memory.form.slugHint')}>
              <input
                required
                pattern="[a-z0-9]+(-[a-z0-9]+)*"
                maxLength={100}
                className={`${INPUT} font-mono`}
                value={slug}
                onChange={(e) => {
                  setSlugTouched(true);
                  setSlug(e.target.value);
                }}
              />
            </Field>
            <Field label={t('memory.form.kind')}>
              <select className={INPUT} value={kind} onChange={(e) => setKind(e.target.value)}>
                {CONTEXT_KINDS.map((k) => (
                  <option key={k} value={k}>{t(`memory.form.kinds.${k}`)}</option>
                ))}
              </select>
            </Field>
          </div>
          <Field label={t('memory.form.description')}>
            <textarea rows={3} className={INPUT} value={description} onChange={(e) => setDescription(e.target.value)} />
          </Field>
          {error && <p role="alert" className="text-sm text-destructive-text">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>{t('memory.actions.cancel')}</Button>
            <Button type="submit" variant="primary" arrow={false} disabled={busy || !name.trim() || !slug}>{busy ? t('memory.actions.creating') : t('memory.actions.create')}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function NewMemoryDialog({ open, contexts, defaultContext, onClose, onCreated, onNewContext }: {
  open: boolean;
  contexts: Context[];
  defaultContext?: string;
  onClose: () => void;
  onCreated: (m: Memory) => void;
  onNewContext: () => void;
}) {
  const { t } = useTranslation();
  const errMsg = useErrorMessage();
  const [context, setContext] = useState(defaultContext ?? '');
  const [type, setType] = useState<MemoryType>('semantic');
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [importance, setImportance] = useState(0.5);
  const [occurredAt, setOccurredAt] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const ctx = context || defaultContext || '';

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      const m = await createMemory({
        content: content.trim(),
        context: ctx,
        type,
        title: title.trim() || undefined,
        importance,
        occurred_at: occurredAt ? new Date(occurredAt).toISOString() : undefined,
      });
      onCreated(m);
      setTitle('');
      setContent('');
      setOccurredAt('');
      onClose();
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && !busy && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{t('memory.form.newMemoryTitle')}</DialogTitle>
          <DialogDescription>{t('memory.form.newMemoryDesc')}</DialogDescription>
        </DialogHeader>
        <form onSubmit={(e) => void submit(e)} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t('memory.context')}>
              <select required className={INPUT} value={ctx} onChange={(e) => setContext(e.target.value)}>
                <option value="" disabled>{contexts.length ? t('memory.form.chooseContext') : t('memory.form.noContexts')}</option>
                {contexts.map((c) => (
                  <option key={c.slug} value={c.slug}>{c.name}</option>
                ))}
              </select>
            </Field>
            <Field label={t('memory.type')}>
              <select className={INPUT} value={type} onChange={(e) => setType(e.target.value as MemoryType)}>
                {MEMORY_TYPES.map((x) => (
                  <option key={x} value={x}>{t(`memory.types.${x}`)}</option>
                ))}
              </select>
            </Field>
          </div>
          <button type="button" onClick={onNewContext} className="-mt-2 text-xs text-brand-soft hover:underline">+ {t('memory.actions.newContext')}</button>
          <Field label={t('memory.form.content')}>
            <textarea autoFocus required rows={5} className={INPUT} value={content} onChange={(e) => setContent(e.target.value)} />
          </Field>
          <Field label={t('memory.form.title')} hint={t('memory.form.titleHint')}>
            <input className={INPUT} value={title} onChange={(e) => setTitle(e.target.value)} />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={`${t('memory.form.importance')}: ${importance.toFixed(2)}`}>
              <input type="range" min={0} max={1} step={0.05} value={importance} onChange={(e) => setImportance(Number(e.target.value))} className="w-full accent-[var(--color-brand-soft)]" />
            </Field>
            {(type === 'episodic' || type === 'decision') && (
              <Field label={t('memory.form.occurredAt')} hint={t('memory.form.occurredAtHint')}>
                <input type="date" className={INPUT} value={occurredAt} onChange={(e) => setOccurredAt(e.target.value)} />
              </Field>
            )}
          </div>
          {error && <p role="alert" className="text-sm text-destructive-text">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>{t('memory.actions.cancel')}</Button>
            <Button type="submit" variant="primary" arrow={false} disabled={busy || !ctx || !content.trim()}>{busy ? t('memory.actions.creating') : t('memory.actions.create')}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Lists the contexts the user can see; edits name/kind/description and deletes (admin-only on the API). */
export function ManageContextsDialog({ open, contexts, onClose, onChanged }: {
  open: boolean;
  contexts: Context[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const errMsg = useErrorMessage();
  const [editing, setEditing] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [kind, setKind] = useState('project');
  const [description, setDescription] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [toDelete, setToDelete] = useState<{ context: Context; count: number } | null>(null);

  const startEdit = (c: Context) => {
    setEditing(c.slug);
    setName(c.name);
    setKind(c.kind);
    setDescription(c.description ?? '');
    setError(undefined);
  };
  const save = async (c: Context) => {
    setBusy(true);
    setError(undefined);
    try {
      await updateContext(c.slug, { name: name.trim(), kind, description: description.trim() });
      setEditing(null);
      onChanged();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };
  const askDelete = async (c: Context) => {
    setError(undefined);
    try {
      setToDelete({ context: c, count: await countContextMemories(c.slug) });
    } catch (e) {
      setError(errMsg(e));
    }
  };
  const doDelete = async () => {
    if (!toDelete) return;
    setBusy(true);
    try {
      await deleteContext(toDelete.context.slug, toDelete.count > 0);
      setToDelete(null);
      onChanged();
    } catch (e) {
      setToDelete(null);
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Dialog open={open} onOpenChange={(o) => !o && !busy && onClose()}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{t('memory.ctxManage.manageTitle')}</DialogTitle>
            <DialogDescription>{t('memory.ctxManage.manageDesc')}</DialogDescription>
          </DialogHeader>
          <ul className="max-h-[55vh] space-y-2 overflow-y-auto pr-1">
            {contexts.length === 0 && <li className="py-6 text-center text-sm text-muted-foreground">{t('memory.ctxManage.none')}</li>}
            {contexts.map((c) => (
              <li key={c.slug} className="rounded-xl border p-3">
                {editing === c.slug ? (
                  <div className="space-y-3">
                    <div className="grid gap-3 sm:grid-cols-[1fr_10rem]">
                      <Field label={t('memory.form.name')}>
                        <input className={INPUT} value={name} onChange={(e) => setName(e.target.value)} />
                      </Field>
                      <Field label={t('memory.form.kind')}>
                        <select className={INPUT} value={kind} onChange={(e) => setKind(e.target.value)}>
                          {[...new Set([...CONTEXT_KINDS, kind])].map((k) => (
                            <option key={k} value={k}>{t(`memory.form.kinds.${k}`, { defaultValue: k })}</option>
                          ))}
                        </select>
                      </Field>
                    </div>
                    <Field label={t('memory.form.description')}>
                      <textarea rows={2} className={INPUT} value={description} onChange={(e) => setDescription(e.target.value)} />
                    </Field>
                    <div className="flex justify-end gap-2">
                      <Button size="sm" variant="ghost" disabled={busy} onClick={() => setEditing(null)}>{t('memory.actions.cancel')}</Button>
                      <Button size="sm" variant="primary" arrow={false} disabled={busy || !name.trim()} onClick={() => void save(c)}>
                        {busy ? t('memory.actions.saving') : t('memory.actions.save')}
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate font-medium">{c.name} <span className="font-mono text-xs font-normal text-muted-foreground">{c.slug}</span></p>
                      <p className="text-xs text-muted-foreground">{t(`memory.form.kinds.${c.kind}`, { defaultValue: c.kind })}{c.description ? ` · ${c.description}` : ''}</p>
                    </div>
                    <div className="flex shrink-0 gap-1">
                      <Button size="sm" variant="ghost" leadingIcon={Pencil} onClick={() => startEdit(c)}>{t('memory.actions.edit')}</Button>
                      <Button size="sm" variant="ghost" className="text-destructive-text" leadingIcon={Trash2} onClick={() => void askDelete(c)}>{t('memory.ctxManage.delete')}</Button>
                    </div>
                  </div>
                )}
              </li>
            ))}
          </ul>
          {error && <p role="alert" className="text-sm text-destructive-text">{error}</p>}
          <DialogFooter>
            <Button variant="tertiary" onClick={onClose} disabled={busy}>{t('memory.ctxManage.done')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={toDelete !== null}
        onOpenChange={(o) => !o && setToDelete(null)}
        title={t('memory.ctxManage.deleteTitle', { name: toDelete?.context.name })}
        description={toDelete && toDelete.count > 0 ? t('memory.ctxManage.deleteWith', { count: toDelete.count }) : t('memory.ctxManage.deleteEmpty')}
        confirmLabel={t('memory.ctxManage.delete')}
        cancelLabel={t('memory.actions.cancel')}
        isPending={busy}
        onConfirm={() => void doDelete()}
      />
    </>
  );
}
