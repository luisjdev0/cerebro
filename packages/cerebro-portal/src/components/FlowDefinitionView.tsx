import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { FlowDiagram } from './FlowDiagram';
import { CopyDownload, ViewTabs } from './MarkdownView';

/** A flow definition shown either as its YAML source or as a flowchart (same Preview/Raw
 *  idea as the Markdown viewer). `version` picks a saved version for the diagram. */
export function FlowDefinitionView({ code, yaml, filename, version }: { code: string; yaml: string; filename: string; version?: number }) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<'diagram' | 'yaml'>('yaml');
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <ViewTabs
          items={[{ id: 'diagram', label: t('flows.detail.diagram') }, { id: 'yaml', label: 'YAML' }]}
          value={mode}
          onChange={(id) => setMode(id as 'diagram' | 'yaml')}
        />
        {mode === 'yaml' && <CopyDownload text={yaml} filename={filename} downloadLabel={t('flows.form.download')} mime="text/yaml;charset=utf-8" />}
      </div>
      {mode === 'yaml' ? (
        <pre className="max-h-[28rem] overflow-auto rounded-xl border bg-card p-4 font-mono text-[13px] leading-relaxed">{yaml}</pre>
      ) : (
        <FlowDiagram code={code} version={version} />
      )}
    </div>
  );
}
