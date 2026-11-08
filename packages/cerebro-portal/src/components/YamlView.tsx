import { useTranslation } from 'react-i18next';
import { CopyDownload } from './MarkdownView';

/** Read-only YAML source with the same copy / download buttons the documents have. */
export function YamlView({ value, filename }: { value: string; filename: string }) {
  const { t } = useTranslation();
  return (
    <div className="space-y-2">
      <div className="flex justify-end">
        <CopyDownload text={value} filename={filename} downloadLabel={t('flows.form.download')} mime="text/yaml;charset=utf-8" />
      </div>
      <pre className="max-h-[28rem] overflow-auto rounded-xl border bg-card p-4 font-mono text-[13px] leading-relaxed">{value}</pre>
    </div>
  );
}
