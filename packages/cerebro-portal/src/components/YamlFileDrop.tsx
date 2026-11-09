import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { FileDrop } from './FileDrop';

const MAX_BYTES = 1024 * 1024;

/** Drop or choose ONE .yaml/.yml file and hand its text to `onText`. */
export function YamlFileDrop({ onText, disabled }: { onText: (text: string, name: string) => void; disabled?: boolean }) {
  const { t } = useTranslation();
  const [error, setError] = useState<string>();
  const take = async (files: File[]) => {
    setError(undefined);
    if (files.length > 1) return setError(t('flows.upload.one'));
    const f = files[0];
    if (!/\.ya?ml$/i.test(f.name)) return setError(t('flows.upload.type'));
    if (f.size > MAX_BYTES) return setError(t('flows.upload.size'));
    const text = await f.text();
    if (!text.trim()) return setError(t('flows.upload.empty'));
    onText(text, f.name);
  };
  return (
    <div className="space-y-2">
      <FileDrop multiple={false} accept=".yaml,.yml" disabled={disabled} onFiles={(f) => void take(f)} title={t('flows.upload.drop')} hint={t('flows.upload.hint')} />
      {error && <p role="alert" className="text-xs text-destructive-text">{error}</p>}
    </div>
  );
}
