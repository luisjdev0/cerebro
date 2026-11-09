import { useEffect, useState, type FormEvent } from 'react';
import { Button, ConfirmDialog, SheetDescription, SheetHeader, SheetTitle } from '@alquimias/ixchel';
import { Pencil, Plus, Trash2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  CONTEXT_KINDS,
  MEMORY_TYPES,
  countContextMemories,
  createContext,
  createMemory,
  deleteContext,
  slugify,
  updateContext,
  type Context,
  type Memory,
  type MemoryType,
} from '../lib/memories';
import { Field, INPUT, useErrorMessage } from './formUi';
import { ResizableSheet } from './ResizableSheet';

function PanelHeader({ title, description }: { title: string; description: string }) {
  return (
    <SheetHeader className="space-y-1.5 border-b bg-muted/30 p-6 pr-12">
      <SheetTitle className="text-xl leading-snug">{title}</SheetTitle>
      <SheetDescription>{description}</SheetDescription>
    </SheetHeader>
  );
}

function PanelFooter({ children }: { children: React.ReactNode }) {
  return <div className="flex items-center justify-end gap-2 border-t p-3">{children}</div>;
}

/** Name / identifier / kind / description. Used to create and to edit a context. */
function ContextForm({ initial, onSubmit, onCancel, busy, error }: {
  initial?: Context;
  onSubmit: (v: { slug: string; name: string; kind: string; description: string }) => void;
  onCancel: () => void;
  busy: boolean;
  error?: string;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState(initial?.name ?? '');
  const [slug, setSlug] = useState(initial?.slug ?? '');
  const [slugTouched, setSlugTouched] = useState(false);
  const [kind, setKind] = useState(initial?.kind ?? 'project');
  const [description, setDescription] = useState(initial?.description ?? '');

  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSubmit({ slug, name: name.trim(), kind, description: description.trim() });
  };

  return (
    <form onSubmit={submit} className="space-y-3 rounded-xl border bg-card p-4">
      <div className="grid gap-3 sm:grid-cols-[1fr_11rem]">
        <Field label={t('memory.form.name')}>
          <input
            autoFocus
            required
            className={INPUT}
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              if (!initial && !slugTouched) setSlug(slugify(e.target.value));
            }}
          />
        </Field>
        <Field label={t('memory.form.kind')}>
          <select className={INPUT} value={kind} onChange={(e) => setKind(e.target.value)}>
            {[...new Set([...CONTEXT_KINDS, kind])].map((k) => (
              <option key={k} value={k}>{t(`memory.form.kinds.${k}`, { defaultValue: k })}</option>
            ))}
          </select>
        </Field>
      </div>
      {!initial && (
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
      )}
      <Field label={t('memory.form.description')}>
        <textarea rows={2} className={INPUT} value={description} onChange={(e) => setDescription(e.target.value)} />
      </Field>
      {error && <p role="alert" className="text-sm text-destructive-text">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={onCancel}>{t('memory.actions.cancel')}</Button>
        <Button type="submit" size="sm" variant="primary" arrow={false} disabled={busy || !name.trim() || !slug}>
          {busy ? t('memory.actions.saving') : initial ? t('memory.actions.save') : t('memory.actions.create')}
        </Button>
      </div>
    </form>
  );
}

/** Side panel to browse, create, edit and delete contexts. */
export function ContextsPanel({ open, contexts, startCreating, onClose, onChanged }: {
  open: boolean;
  contexts: Context[];
  startCreating?: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const errMsg = useErrorMessage();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [toDelete, setToDelete] = useState<{ context: Context; count: number } | null>(null);
  const showCreate = creating;
  useEffect(() => {
    if (open && startCreating) setCreating(true);
  }, [open, startCreating]);

  const run = async (fn: () => Promise<void>, done: () => void) => {
    setBusy(true);
    setError(undefined);
    try {
      await fn();
      done();
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
    const { context, count } = toDelete;
    setToDelete(null);
    await run(() => deleteContext(context.slug, count > 0).then(() => undefined), () => undefined);
  };

  const close = () => {
    setCreating(false);
    setEditing(null);
    setError(undefined);
    onClose();
  };

  return (
    <ResizableSheet open={open} onClose={close}>
      <PanelHeader title={t('memory.ctxManage.manageTitle')} description={t('memory.ctxManage.manageDesc')} />
      <div className="flex-1 space-y-3 overflow-y-auto p-6">
        {showCreate ? (
          <ContextForm
            busy={busy}
            error={error}
            onCancel={() => setCreating(false)}
            onSubmit={(v) =>
              void run(
                () => createContext({ slug: v.slug, name: v.name, kind: v.kind, description: v.description || undefined }).then(() => undefined),
                () => setCreating(false),
              )
            }
          />
        ) : (
          <Button variant="tertiary" leadingIcon={Plus} onClick={() => { setCreating(true); setError(undefined); }}>
            {t('memory.actions.newContext')}
          </Button>
        )}
        {!showCreate && error && <p role="alert" className="text-sm text-destructive-text">{error}</p>}

        <ul className="space-y-2">
          {contexts.length === 0 && <li className="py-6 text-center text-sm text-muted-foreground">{t('memory.ctxManage.none')}</li>}
          {contexts.map((c) => (
            <li key={c.slug}>
              {editing === c.slug ? (
                <ContextForm
                  initial={c}
                  busy={busy}
                  error={error}
                  onCancel={() => setEditing(null)}
                  onSubmit={(v) =>
                    void run(
                      () => updateContext(c.slug, { name: v.name, kind: v.kind, description: v.description }).then(() => undefined),
                      () => setEditing(null),
                    )
                  }
                />
              ) : (
                <div className="flex items-start justify-between gap-3 rounded-xl border p-3">
                  <div className="min-w-0">
                    <p className="truncate font-medium">{c.name} <span className="font-mono text-xs font-normal text-muted-foreground">{c.slug}</span></p>
                    <p className="text-xs text-muted-foreground">{t(`memory.form.kinds.${c.kind}`, { defaultValue: c.kind })}{c.description ? ` · ${c.description}` : ''}</p>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <Button size="sm" variant="ghost" leadingIcon={Pencil} onClick={() => { setEditing(c.slug); setError(undefined); }}>{t('memory.actions.edit')}</Button>
                    <Button size="sm" variant="ghost" className="text-destructive-text" leadingIcon={Trash2} onClick={() => void askDelete(c)}>{t('memory.ctxManage.delete')}</Button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      </div>
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
    </ResizableSheet>
  );
}

/** Side panel with the form to create a memory. */
export function NewMemoryPanel({ open, contexts, defaultContext, onClose, onCreated, onNewContext }: {
  open: boolean;
  contexts: Context[];
  defaultContext?: string;
  onClose: () => void;
  onCreated: (m: Memory) => void;
  onNewContext: () => void;
}) {
  const { t } = useTranslation();
  const errMsg = useErrorMessage();
  const [context, setContext] = useState('');
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
      setTitle('');
      setContent('');
      setOccurredAt('');
      setImportance(0.5);
      onCreated(m);
      onClose();
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <ResizableSheet open={open} onClose={() => !busy && onClose()}>
      <PanelHeader title={t('memory.form.newMemoryTitle')} description={t('memory.form.newMemoryDesc')} />
      <form id="new-memory-form" onSubmit={(e) => void submit(e)} className="flex-1 space-y-5 overflow-y-auto p-6">
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
        <button type="button" onClick={onNewContext} className="-mt-3 text-xs text-brand-soft hover:underline">+ {t('memory.actions.newContext')}</button>
        <Field label={t('memory.form.content')}>
          <textarea autoFocus required rows={8} className={`${INPUT} leading-relaxed`} value={content} onChange={(e) => setContent(e.target.value)} />
        </Field>
        <Field label={t('memory.form.title')} hint={t('memory.form.titleHint')}>
          <input className={INPUT} value={title} onChange={(e) => setTitle(e.target.value)} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={`${t('memory.form.importance')}: ${importance.toFixed(2)}`}>
            <input type="range" min={0} max={1} step={0.05} value={importance} onChange={(e) => setImportance(Number(e.target.value))} className="mt-2 w-full accent-[var(--color-brand-soft)]" />
          </Field>
          {(type === 'episodic' || type === 'decision') && (
            <Field label={t('memory.form.occurredAt')} hint={t('memory.form.occurredAtHint')}>
              <input type="date" className={INPUT} value={occurredAt} onChange={(e) => setOccurredAt(e.target.value)} />
            </Field>
          )}
        </div>
        {error && <p role="alert" className="text-sm text-destructive-text">{error}</p>}
      </form>
      <PanelFooter>
        <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>{t('memory.actions.cancel')}</Button>
        <Button type="submit" form="new-memory-form" variant="primary" arrow={false} disabled={busy || !ctx || !content.trim()}>
          {busy ? t('memory.actions.creating') : t('memory.actions.create')}
        </Button>
      </PanelFooter>
    </ResizableSheet>
  );
}
