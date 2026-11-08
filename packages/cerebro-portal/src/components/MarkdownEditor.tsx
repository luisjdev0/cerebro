import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { INPUT } from './formUi';
import { Markdown } from './Markdown';

/** Textarea with a Write / Preview switch so what gets saved can be checked as it will read. */
export function MarkdownEditor({ value, onChange, rows = 16, autoFocus }: { value: string; onChange: (v: string) => void; rows?: number; autoFocus?: boolean }) {
  const { t } = useTranslation();
  const [preview, setPreview] = useState(false);
  const tab = (on: boolean) =>
    `rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${on ? 'bg-foreground text-background' : 'text-muted-foreground hover:text-foreground'}`;
  return (
    <div className="space-y-2">
      <div className="inline-flex gap-1 rounded-lg border p-0.5">
        <button type="button" className={tab(!preview)} onClick={() => setPreview(false)}>{t('docs.form.write')}</button>
        <button type="button" className={tab(preview)} onClick={() => setPreview(true)}>{t('docs.form.preview')}</button>
      </div>
      {preview ? (
        <div className="min-h-40 rounded-xl border bg-card p-4"><Markdown>{value || ' '}</Markdown></div>
      ) : (
        <textarea autoFocus={autoFocus} required rows={rows} className={`${INPUT} font-mono text-[13px] leading-relaxed`} value={value} onChange={(e) => onChange(e.target.value)} />
      )}
    </div>
  );
}
