import { useRef, useState } from 'react';
import { UploadCloud } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { ACCEPT } from '../lib/importDocs';

/** Drag-and-drop area (also clickable) that hands the chosen files to `onFiles`. */
export function FileDrop({ onFiles, disabled, multiple = true, accept = ACCEPT, title, hint }: { onFiles: (files: File[]) => void; disabled?: boolean; multiple?: boolean; accept?: string; title?: string; hint?: string }) {
  const { t } = useTranslation();
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <div
      role="button"
      tabIndex={0}
      aria-disabled={disabled}
      onClick={() => !disabled && input.current?.click()}
      onKeyDown={(e) => {
        if (!disabled && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault();
          input.current?.click();
        }
      }}
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const files = Array.from(e.dataTransfer.files);
        if (!disabled && files.length) onFiles(files);
      }}
      className={`flex cursor-pointer flex-col items-center gap-1.5 rounded-xl border-2 border-dashed p-5 text-center transition-colors ${over ? 'border-brand-soft bg-brand-soft/10' : 'hover:bg-muted/50'} ${disabled ? 'pointer-events-none opacity-60' : ''}`}
    >
      <UploadCloud className="size-6 text-muted-foreground" />
      <p className="text-sm font-medium">{title ?? t('docs.import.drop')}</p>
      <p className="text-xs text-muted-foreground">{hint ?? t('docs.import.dropHint')}</p>
      <input
        ref={input}
        type="file"
        multiple={multiple}
        accept={accept}
        className="hidden"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = ''; // let the same file be chosen again
          if (files.length) onFiles(files);
        }}
      />
    </div>
  );
}
