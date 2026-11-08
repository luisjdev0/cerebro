import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { INPUT } from './formUi';
import { Markdown } from './Markdown';
import { CopyButton, ViewTabs } from './MarkdownView';

/** Textarea with a Write / Preview switch so what gets saved can be checked as it will read. */
export function MarkdownEditor({ value, onChange, rows = 16, autoFocus }: { value: string; onChange: (v: string) => void; rows?: number; autoFocus?: boolean }) {
  const { t } = useTranslation();
  const [preview, setPreview] = useState(false);
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <ViewTabs
          items={[{ id: 'write', label: t('docs.form.write') }, { id: 'preview', label: t('docs.form.preview') }]}
          value={preview ? 'preview' : 'write'}
          onChange={(id) => setPreview(id === 'preview')}
        />
        <CopyButton text={value} />
      </div>
      {preview ? (
        <div className="min-h-40 rounded-xl border bg-card p-4"><Markdown>{value || ' '}</Markdown></div>
      ) : (
        <textarea autoFocus={autoFocus} required rows={rows} className={`${INPUT} font-mono text-[13px] leading-relaxed`} value={value} onChange={(e) => onChange(e.target.value)} />
      )}
    </div>
  );
}
