import { useEffect, useState, type FormEvent } from 'react';
import { Button, ConfirmDialog, SheetDescription, SheetHeader, SheetTitle } from '@alquimias/ixchel';
import { Pencil, Plus, Trash2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  countCategoryDocuments,
  createCategory,
  createDocument,
  deleteCategory,
  updateCategory,
  type Category,
  type Doc,
} from '../lib/documents';
import { slugify } from '../lib/memories';
import { Field, INPUT, useErrorMessage } from './formUi';
import { MarkdownEditor } from './MarkdownEditor';
import { ResizableSheet } from './ResizableSheet';

function PanelHeader({ title, description }: { title: string; description: string }) {
  return (
    <SheetHeader className="space-y-1.5 border-b bg-muted/30 p-6 pr-12">
      <SheetTitle className="text-xl leading-snug">{title}</SheetTitle>
      <SheetDescription>{description}</SheetDescription>
    </SheetHeader>
  );
}

/** Side panel with the form to create a document. */
export function NewDocumentPanel({ open, categories, defaultCategory, onClose, onCreated, onNewCategory }: {
  open: boolean;
  categories: Category[];
  defaultCategory?: string;
  onClose: () => void;
  onCreated: (d: Doc) => void;
  onNewCategory: () => void;
}) {
  const { t } = useTranslation();
  const errMsg = useErrorMessage();
  const [category, setCategory] = useState('');
  const [title, setTitle] = useState('');
  const [slug, setSlug] = useState('');
  const [content, setContent] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const cat = category || defaultCategory || '';

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      const d = await createDocument({ title: title.trim(), content, category: cat, slug: slug.trim() || undefined });
      setTitle('');
      setSlug('');
      setContent('');
      onCreated(d);
      onClose();
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <ResizableSheet open={open} onClose={() => !busy && onClose()}>
      <PanelHeader title={t('docs.form.newTitle')} description={t('docs.form.newDesc')} />
      <form id="new-doc-form" onSubmit={(e) => void submit(e)} className="flex-1 space-y-5 overflow-y-auto p-6">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t('docs.form.category')}>
            <select required className={INPUT} value={cat} onChange={(e) => setCategory(e.target.value)}>
              <option value="" disabled>{categories.length ? t('docs.form.chooseCategory') : t('docs.form.noCategories')}</option>
              {categories.map((c) => (
                <option key={c.slug} value={c.slug}>{c.name}</option>
              ))}
            </select>
          </Field>
          <Field label={t('docs.form.slug')} hint={t('docs.form.slugHint')}>
            <input className={`${INPUT} font-mono`} pattern="[a-z0-9]+(-[a-z0-9]+)*" value={slug} onChange={(e) => setSlug(e.target.value)} />
          </Field>
        </div>
        <button type="button" onClick={onNewCategory} className="-mt-3 text-xs text-brand-soft hover:underline">+ {t('docs.actions.newCategory')}</button>
        <Field label={t('docs.form.title')}>
          <input autoFocus required className={INPUT} value={title} onChange={(e) => setTitle(e.target.value)} />
        </Field>
        <Field label={t('docs.form.content')}>
          <MarkdownEditor value={content} onChange={setContent} />
        </Field>
        {error && <p role="alert" className="text-sm text-destructive-text">{error}</p>}
      </form>
      <div className="flex items-center justify-end gap-2 border-t p-3">
        <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>{t('docs.actions.cancel')}</Button>
        <Button type="submit" form="new-doc-form" variant="primary" arrow={false} disabled={busy || !cat || !title.trim() || !content.trim()}>
          {busy ? t('docs.actions.creating') : t('docs.actions.create')}
        </Button>
      </div>
    </ResizableSheet>
  );
}

function CategoryForm({ initial, onSubmit, onCancel, busy, error }: {
  initial?: Category;
  onSubmit: (v: { slug: string; name: string; description: string; hidden: boolean }) => void;
  onCancel: () => void;
  busy: boolean;
  error?: string;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState(initial?.name ?? '');
  const [slug, setSlug] = useState(initial?.slug ?? '');
  const [slugTouched, setSlugTouched] = useState(false);
  const [description, setDescription] = useState(initial?.description ?? '');
  const [hidden, setHidden] = useState(initial?.hidden ?? false);

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit({ slug, name: name.trim(), description: description.trim(), hidden });
      }}
      className="space-y-3 rounded-xl border bg-card p-4"
    >
      <Field label={t('docs.cat.name')}>
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
      {!initial && (
        <Field label={t('docs.cat.slug')} hint={t('docs.cat.slugHint')}>
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
      <Field label={t('docs.cat.description')}>
        <textarea rows={2} className={INPUT} value={description} onChange={(e) => setDescription(e.target.value)} />
      </Field>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={hidden} disabled={initial?.locked} onChange={(e) => setHidden(e.target.checked)} />
        {t('docs.cat.hidden')}
      </label>
      {error && <p role="alert" className="text-sm text-destructive-text">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={onCancel}>{t('docs.actions.cancel')}</Button>
        <Button type="submit" size="sm" variant="primary" arrow={false} disabled={busy || !name.trim() || !slug}>
          {busy ? t('docs.actions.saving') : initial ? t('docs.actions.save') : t('docs.actions.create')}
        </Button>
      </div>
    </form>
  );
}

/** Side panel to browse, create, edit and delete categories. */
export function CategoriesPanel({ open, categories, startCreating, onClose, onChanged }: {
  open: boolean;
  categories: Category[];
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
  const [toDelete, setToDelete] = useState<{ category: Category; count: number } | null>(null);
  useEffect(() => {
    if (open && startCreating) setCreating(true);
  }, [open, startCreating]);

  const run = async (fn: () => Promise<unknown>, done: () => void) => {
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
  const askDelete = async (c: Category) => {
    setError(undefined);
    try {
      setToDelete({ category: c, count: await countCategoryDocuments(c.slug) });
    } catch (e) {
      setError(errMsg(e));
    }
  };
  const doDelete = async () => {
    if (!toDelete) return;
    const { category, count } = toDelete;
    setToDelete(null);
    await run(() => deleteCategory(category.slug, count > 0), () => undefined);
  };
  const close = () => {
    setCreating(false);
    setEditing(null);
    setError(undefined);
    onClose();
  };

  return (
    <ResizableSheet open={open} onClose={close}>
      <PanelHeader title={t('docs.cat.title')} description={t('docs.cat.desc')} />
      <div className="flex-1 space-y-3 overflow-y-auto p-6">
        {creating ? (
          <CategoryForm
            busy={busy}
            error={error}
            onCancel={() => setCreating(false)}
            onSubmit={(v) => void run(() => createCategory({ slug: v.slug, name: v.name, description: v.description || undefined, hidden: v.hidden }), () => setCreating(false))}
          />
        ) : (
          <Button variant="tertiary" leadingIcon={Plus} onClick={() => { setCreating(true); setError(undefined); }}>{t('docs.actions.newCategory')}</Button>
        )}
        {!creating && error && <p role="alert" className="text-sm text-destructive-text">{error}</p>}
        <ul className="space-y-2">
          {categories.length === 0 && <li className="py-6 text-center text-sm text-muted-foreground">{t('docs.cat.none')}</li>}
          {categories.map((c) => (
            <li key={c.slug}>
              {editing === c.slug ? (
                <CategoryForm
                  initial={c}
                  busy={busy}
                  error={error}
                  onCancel={() => setEditing(null)}
                  onSubmit={(v) => void run(() => updateCategory(c.slug, { name: v.name, description: v.description, hidden: v.hidden }), () => setEditing(null))}
                />
              ) : (
                <div className="flex items-start justify-between gap-3 rounded-xl border p-3">
                  <div className="min-w-0">
                    <p className="truncate font-medium">
                      {c.name} <span className="font-mono text-xs font-normal text-muted-foreground">{c.slug}</span>
                      {c.hidden && <span className="ml-2 rounded bg-muted px-1.5 py-0.5 text-[10px] font-semibold uppercase">{t('docs.cat.hiddenBadge')}</span>}
                      {c.locked && <span className="ml-1 rounded bg-muted px-1.5 py-0.5 text-[10px] font-semibold uppercase">{t('docs.cat.lockedBadge')}</span>}
                    </p>
                    {c.description && <p className="text-xs text-muted-foreground">{c.description}</p>}
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <Button size="sm" variant="ghost" leadingIcon={Pencil} onClick={() => { setEditing(c.slug); setError(undefined); }}>{t('docs.actions.edit')}</Button>
                    <Button size="sm" variant="ghost" className="text-destructive-text" leadingIcon={Trash2} onClick={() => void askDelete(c)}>{t('docs.actions.delete')}</Button>
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
        title={t('docs.cat.deleteTitle', { name: toDelete?.category.name })}
        description={toDelete && toDelete.count > 0 ? t('docs.cat.deleteWith', { count: toDelete.count }) : t('docs.cat.deleteEmpty')}
        confirmLabel={t('docs.actions.delete')}
        cancelLabel={t('docs.actions.cancel')}
        isPending={busy}
        onConfirm={() => void doDelete()}
      />
    </ResizableSheet>
  );
}
