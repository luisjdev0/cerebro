import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { Badge, Button, ConfirmDialog, SheetDescription, SheetHeader, SheetTitle } from '@alquimias/ixchel';
import { CheckCircle2, Pencil, Plus, Trash2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  createFlow,
  deleteFlow,
  getFlow,
  getFlowReferences,
  getFlowVersions,
  parseDocRef,
  updateFlow,
  validateFlow,
  type Flow,
  type FlowCategory,
  type FlowReference,
  type FlowVersion,
} from '../lib/flows';
import { api } from '../lib/api';
import { getDocument, listCategories as listDocCategories, type Category as DocCategory } from '../lib/documents';
import { slugify } from '../lib/memories';
import { Field, INPUT, useErrorMessage } from './formUi';
import { useDateTime } from './memoryUi';
import { ResizableSheet } from './ResizableSheet';
import { FlowDefinitionView } from './FlowDefinitionView';
import { YamlFileDrop } from './YamlFileDrop';
import { DocumentDetail, type DocRef } from './DocumentDetail';
import { RefDocPanel, type MissingRef } from './RefDocPanel';

const NEW_CATEGORY = '__new__';
const YAML_AREA = `${INPUT} font-mono text-[13px] leading-relaxed`;

const template = (category: string) => `metadata:
  name: My flow
  category: ${category || 'category'}
  description: What this flow is for
entry: first
procedure:
  - id: first
    type: task
    description: Do the first thing
    next: review
  - id: review
    type: task
    description: Wait for a person to check the result
    checkpoint:
      required: true
      prompt: Is the result correct?
      on_reject: first
    next: done
  - id: done
    type: task
    terminal: true
`;

/** Validate button + its feedback, shared by the create and edit forms. */
function ValidateRow({ yaml, disabled }: { yaml: string; disabled?: boolean }) {
  const { t } = useTranslation();
  const [state, setState] = useState<{ ok: boolean; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setState(null), [yaml]);
  return (
    <div className="space-y-2">
      <Button
        type="button"
        size="sm"
        variant="tertiary"
        leadingIcon={CheckCircle2}
        disabled={busy || disabled || !yaml.trim()}
        onClick={async () => {
          setBusy(true);
          try {
            await validateFlow(yaml);
            setState({ ok: true, message: t('flows.form.valid') });
          } catch (e) {
            setState({ ok: false, message: e instanceof Error ? e.message : t('memory.errors.generic') });
          } finally {
            setBusy(false);
          }
        }}
      >
        {t('flows.form.validate')}
      </Button>
      {state && (
        <p role={state.ok ? 'status' : 'alert'} className={`text-sm ${state.ok ? 'text-brand-soft' : 'text-destructive-text'}`}>
          {state.message}
        </p>
      )}
    </div>
  );
}

function PanelHeader({ title, description }: { title: string; description: string }) {
  return (
    <SheetHeader className="space-y-1.5 border-b bg-muted/30 p-6 pr-12">
      <SheetTitle className="text-xl leading-snug">{title}</SheetTitle>
      <SheetDescription>{description}</SheetDescription>
    </SheetHeader>
  );
}

/** Side panel to define a new flow from its YAML (a starter template is offered). */
export function NewFlowPanel({ open, categories, defaultCategory, onClose, onCreated, onCategoryCreated }: {
  open: boolean;
  categories: FlowCategory[];
  defaultCategory?: string;
  onClose: () => void;
  onCreated: (f: Flow) => void;
  onCategoryCreated: () => void;
}) {
  const { t } = useTranslation();
  const errMsg = useErrorMessage();
  const [category, setCategory] = useState('');
  const [yaml, setYaml] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [newCat, setNewCat] = useState(false);
  const [catName, setCatName] = useState('');
  const [catCode, setCatCode] = useState('');

  useEffect(() => {
    if (!open) return;
    const initial = defaultCategory || categories[0]?.slug || '';
    setCategory(initial);
    setYaml(template(initial));
    setError(undefined);
    setNewCat(false);
    setCatName('');
    setCatCode('');
    // only when the panel opens
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      onCreated(await createFlow(category, yaml));
      onClose();
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  const createCat = async () => {
    setBusy(true);
    setError(undefined);
    try {
      const c = await api<FlowCategory>('/flows/categories', { method: 'POST', json: { slug: slugify(catName), code: catCode.trim().toUpperCase(), name: catName.trim() } });
      onCategoryCreated();
      setCategory(c.slug);
      setNewCat(false);
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  const noCategories = categories.length === 0;
  return (
    <ResizableSheet open={open} onClose={() => !busy && onClose()}>
      <PanelHeader title={t('flows.form.newTitle')} description={t('flows.form.newDesc')} />
      <form onSubmit={submit} className="flex min-h-0 flex-1 flex-col">
        <div className="flex-1 space-y-5 overflow-y-auto p-6">
          {(noCategories || newCat) ? (
            <div className="space-y-3 rounded-xl border bg-muted/30 p-4">
              <p className="text-sm font-semibold">{t('flows.cat.newTitle')}</p>
              {noCategories && <p className="text-xs text-muted-foreground">{t('flows.form.noCategories')}</p>}
              <Field label={t('flows.cat.name')}>
                <input className={INPUT} value={catName} onChange={(e) => setCatName(e.target.value)} />
              </Field>
              <Field label={t('flows.cat.code')} hint={t('flows.cat.codeHint')}>
                <input className={INPUT} value={catCode} maxLength={8} onChange={(e) => setCatCode(e.target.value.replace(/[^a-zA-Z0-9]/g, ''))} />
              </Field>
              <div className="flex justify-end gap-2">
                {!noCategories && <Button type="button" size="sm" variant="ghost" onClick={() => setNewCat(false)}>{t('docs.actions.cancel')}</Button>}
                <Button type="button" size="sm" variant="primary" arrow={false} disabled={busy || !catName.trim() || !catCode.trim()} onClick={() => void createCat()}>{t('flows.cat.create')}</Button>
              </div>
            </div>
          ) : (
            <Field label={t('docs.form.category')}>
              <select
                className={INPUT}
                value={category}
                onChange={(e) => (e.target.value === NEW_CATEGORY ? setNewCat(true) : setCategory(e.target.value))}
              >
                {categories.map((c) => (
                  <option key={c.slug} value={c.slug}>{c.name} ({c.code})</option>
                ))}
                <option value={NEW_CATEGORY}>＋ {t('flows.cat.new')}…</option>
              </select>
            </Field>
          )}
          <YamlFileDrop disabled={busy} onText={(text) => setYaml(text)} />
          <Field label={t('flows.form.yaml')} hint={t('flows.form.yamlHint')}>
            <textarea required rows={20} spellCheck={false} className={YAML_AREA} value={yaml} onChange={(e) => setYaml(e.target.value)} />
          </Field>
          <ValidateRow yaml={yaml} />
          {error && <p role="alert" className="text-sm text-destructive-text">{error}</p>}
        </div>
        <div className="flex justify-end gap-2 border-t p-3">
          <Button type="button" variant="ghost" disabled={busy} onClick={onClose}>{t('docs.actions.cancel')}</Button>
          <Button type="submit" variant="primary" arrow={false} disabled={busy || !category || !yaml.trim()}>
            {busy ? t('docs.actions.creating') : t('docs.actions.create')}
          </Button>
        </div>
      </form>
    </ResizableSheet>
  );
}


type ResolvedRef = FlowReference & { doc: DocRef | null; title: string | null; found: boolean };

/** Cerebro documents the flow points at (metadata references and delegate prompts), resolved to their titles. */
function RelatedDocuments({ code, reloadKey, onOpen, onUpload }: { code: string; reloadKey: number; onOpen: (r: DocRef) => void; onUpload: (r: MissingRef) => void }) {
  const { t } = useTranslation();
  const [items, setItems] = useState<ResolvedRef[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    setItems(null);
    getFlowReferences(code)
      .then((refs) =>
        Promise.all(
          refs.map(async (r): Promise<ResolvedRef> => {
            const doc = parseDocRef(r.ref);
            if (!doc) return { ...r, doc: null, title: null, found: false };
            try {
              const d = await getDocument(doc.category, doc.slug);
              return { ...r, doc: { category: d.category, slug: d.slug }, title: d.title, found: true };
            } catch {
              return { ...r, doc, title: null, found: false };
            }
          }),
        ),
      )
      .then((r) => !cancelled && setItems(r), () => !cancelled && setItems([]));
    return () => {
      cancelled = true;
    };
  }, [code, reloadKey]);

  return (
    <section className="space-y-3">
      <h3 className="text-xs font-bold tracking-wider text-muted-foreground uppercase">{t('flows.detail.related')}</h3>
      {items === null ? (
        <div className="h-10 animate-pulse rounded bg-muted" />
      ) : items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('flows.detail.noRelated')}</p>
      ) : (
        <ul className="space-y-2">
          {items.map((r, i) => (
            <li key={`${r.ref}-${i}`} className="flex items-stretch gap-2">
              <button
                type="button"
                disabled={!r.found}
                onClick={() => r.doc && onOpen(r.doc)}
                className="flex min-w-0 flex-1 items-center justify-between gap-3 rounded-xl border p-3 text-left transition-colors enabled:hover:bg-muted disabled:cursor-default"
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium">{r.title ?? r.ref}</span>
                  <span className="block truncate font-mono text-[11px] text-muted-foreground">{r.doc ? `${r.doc.category}/${r.doc.slug}` : r.ref}</span>
                </span>
                <span className="flex shrink-0 items-center gap-1.5">
                  <Badge variant="outline">{r.kind === 'prompt' ? t('flows.detail.refPrompt', { step: r.step_id }) : t('flows.detail.refMeta')}</Badge>
                  {!r.found && <Badge variant="warning">{t('flows.detail.refMissing')}</Badge>}
                </span>
              </button>
              {!r.found && r.doc && (
                <Button size="sm" variant="tertiary" leadingIcon={Plus} className="shrink-0 self-center" onClick={() => r.doc && onUpload(r.doc)}>{t('flows.refDoc.upload')}</Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Right-hand panel: one flow definition, its YAML, version history, editing and deleting. */
export function FlowDetail({ code, onClose, onChanged }: { code: string | null; onClose: () => void; onChanged: () => void }) {
  const { t } = useTranslation();
  const fmt = useDateTime();
  const errMsg = useErrorMessage();
  const [flow, setFlow] = useState<Flow | null>(null);
  const [versions, setVersions] = useState<FlowVersion[]>([]);
  const [shown, setShown] = useState<number | null>(null);
  const [error, setError] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string>();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [docRef, setDocRef] = useState<DocRef | null>(null);
  const [uploadRef, setUploadRef] = useState<MissingRef | null>(null);
  const [refsKey, setRefsKey] = useState(0);
  const [docCategories, setDocCategories] = useState<DocCategory[]>([]);
  useEffect(() => {
    if (docRef && docCategories.length === 0) listDocCategories().then(setDocCategories, () => undefined);
  }, [docRef]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    setDocRef(null);
    setUploadRef(null);
  }, [code]);

  const load = useCallback((c: string) => {
    let cancelled = false;
    setFlow(null);
    setVersions([]);
    setShown(null);
    setError(false);
    setEditing(false);
    setActionError(undefined);
    getFlow(c).then(
      (f) => {
        if (cancelled) return;
        setFlow(f);
        getFlowVersions(c).then((v) => !cancelled && setVersions(v), () => undefined); // history is a nice-to-have
      },
      () => !cancelled && setError(true),
    );
    return () => {
      cancelled = true;
    };
  }, []);
  useEffect(() => (code ? load(code) : undefined), [code]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    if (!flow) return;
    setBusy(true);
    setActionError(undefined);
    try {
      const next = await updateFlow(flow.code, draft);
      setFlow(next);
      setEditing(false);
      onChanged();
      getFlowVersions(next.code).then(setVersions, () => undefined);
    } catch (e) {
      setActionError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!flow) return;
    setBusy(true);
    try {
      await deleteFlow(flow.code);
      setConfirmDelete(false);
      onChanged();
      onClose();
    } catch (e) {
      setConfirmDelete(false);
      setActionError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
    <ResizableSheet open={code !== null} onClose={onClose}>
      <SheetHeader className="space-y-2 border-b bg-muted/30 p-6 pr-12">
        <div className="flex flex-wrap items-center gap-1.5">
          {flow && (
            <>
              <Badge variant="outline">{flow.category}</Badge>
              <Badge variant="secondary" className="font-mono">{flow.code}</Badge>
              <Badge variant="info">v{flow.current_version}</Badge>
            </>
          )}
        </div>
        <SheetTitle className="text-xl leading-snug">{editing ? t('flows.form.editTitle') : flow?.name || (error ? t('flows.detail.loadError') : '…')}</SheetTitle>
        <SheetDescription className={flow?.description ? '' : 'sr-only'}>{flow?.description || flow?.name}</SheetDescription>
      </SheetHeader>

      <div className="flex-1 space-y-7 overflow-y-auto p-6">
        {flow && editing && (
          <div className="space-y-4">
            <YamlFileDrop disabled={busy} onText={(text) => setDraft(text)} />
            <Field label={t('flows.form.yaml')} hint={t('flows.form.editNote')}>
              <textarea autoFocus rows={22} spellCheck={false} className={YAML_AREA} value={draft} onChange={(e) => setDraft(e.target.value)} />
            </Field>
            <ValidateRow yaml={draft} />
            {actionError && <p role="alert" className="text-sm text-destructive-text">{actionError}</p>}
            <div className="flex justify-end gap-2">
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => setEditing(false)}>{t('docs.actions.cancel')}</Button>
              <Button size="sm" variant="primary" arrow={false} disabled={busy || !draft.trim() || draft === flow.yaml_content} onClick={() => void save()}>
                {busy ? t('docs.actions.saving') : t('docs.actions.save')}
              </Button>
            </div>
          </div>
        )}

        {flow && !editing && (
          <>
            <section className="space-y-3">
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-xs font-bold tracking-wider text-muted-foreground uppercase">{t('flows.detail.definition')}</h3>
                <Button size="sm" variant="ghost" leadingIcon={Pencil} onClick={() => { setDraft(flow.yaml_content); setEditing(true); setActionError(undefined); }}>{t('docs.actions.edit')}</Button>
              </div>
              <FlowDefinitionView code={flow.code} yaml={flow.yaml_content} filename={`${flow.code}.yaml`} />
              {actionError && <p role="alert" className="text-sm text-destructive-text">{actionError}</p>}
            </section>

            <RelatedDocuments code={flow.code} reloadKey={refsKey} onOpen={setDocRef} onUpload={setUploadRef} />

            <section className="space-y-3">
              <h3 className="text-xs font-bold tracking-wider text-muted-foreground uppercase">{t('docs.detail.details')}</h3>
              <dl className="grid grid-cols-2 gap-3 text-sm">
                {[
                  [t('flows.detail.code'), <span className="font-mono text-xs">{flow.code}</span>],
                  [t('docs.createdBy'), flow.created_by ?? '—'],
                  [t('docs.created'), fmt(flow.created_at)],
                  [t('docs.updated'), fmt(flow.updated_at)],
                ].map(([label, value]) => (
                  <div key={String(label)} className="rounded-xl border bg-muted/30 p-3">
                    <dt className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">{label}</dt>
                    <dd className="mt-1 font-semibold">{value}</dd>
                  </div>
                ))}
              </dl>
            </section>

            <section className="space-y-3">
              <h3 className="text-xs font-bold tracking-wider text-muted-foreground uppercase">{t('docs.detail.versions')}</h3>
              {versions.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t('flows.detail.noVersions')}</p>
              ) : (
                <ul className="space-y-2">
                  {versions.map((v) => (
                    <li key={v.version_number} className="rounded-xl border">
                      <div className="flex items-center justify-between gap-3 p-3">
                        <p className="text-sm font-medium">
                          {t('docs.detail.version', { n: v.version_number })}
                          {v.version_number === flow.current_version && <span className="ml-2 text-xs font-normal text-brand-soft">{t('docs.detail.current')}</span>}
                          <span className="font-normal text-muted-foreground"> · {fmt(v.created_at)}</span>
                        </p>
                        <Button size="sm" variant="ghost" onClick={() => setShown(shown === v.version_number ? null : v.version_number)}>
                          {shown === v.version_number ? t('docs.detail.hideVersion') : t('docs.detail.showVersion')}
                        </Button>
                      </div>
                      {shown === v.version_number && (
                        <div className="border-t bg-muted/20 p-4"><FlowDefinitionView code={flow.code} version={v.version_number} yaml={v.yaml_content} filename={`${flow.code}-v${v.version_number}.yaml`} /></div>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </div>

      {flow && !editing && (
        <div className="flex items-center justify-between gap-2 border-t p-3">
          <Button size="sm" variant="ghost" className="text-destructive-text" leadingIcon={Trash2} onClick={() => setConfirmDelete(true)}>{t('docs.actions.delete')}</Button>
        </div>
      )}

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={(o) => !o && setConfirmDelete(false)}
        title={t('flows.deleteTitle')}
        description={t('flows.deleteDesc')}
        confirmLabel={t('docs.actions.delete')}
        cancelLabel={t('docs.actions.cancel')}
        isPending={busy}
        onConfirm={() => void remove()}
      />
    </ResizableSheet>
    <RefDocPanel
      target={uploadRef}
      onClose={() => setUploadRef(null)}
      onCreated={(d) => {
        setUploadRef(null);
        setRefsKey((k) => k + 1);
        setDocRef({ category: d.category, slug: d.slug });
      }}
    />
    <DocumentDetail docRef={docRef} categories={docCategories} onOpen={setDocRef} onClose={() => setDocRef(null)} onChanged={() => undefined} />
    </>
  );
}
