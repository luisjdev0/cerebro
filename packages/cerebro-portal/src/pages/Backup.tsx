import { useState } from 'react';
import { DatabaseBackup } from 'lucide-react';
import { Button, PageHeader } from '@alquimias/ixchel';
import { useTranslation } from 'react-i18next';
import { useAdminError } from '../components/adminUi';
import { downloadBackup } from '../lib/backup';

export function Backup() {
  const { t } = useTranslation();
  const errMsg = useAdminError();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [done, setDone] = useState<{ filename: string; bytes: number }>();

  const run = async () => {
    setBusy(true);
    setError(undefined);
    setDone(undefined);
    try {
      setDone(await downloadBackup());
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader title={t('admin.backup.title')} description={t('admin.backup.description')} />
      <div className="max-w-2xl space-y-4 rounded-2xl border bg-card p-5">
        <p className="text-sm">{t('admin.backup.what')}</p>
        <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
          <li>{t('admin.backup.note1')}</li>
          <li>{t('admin.backup.note2')}</li>
          <li>{t('admin.backup.note3')}</li>
        </ul>
        <Button leadingIcon={DatabaseBackup} disabled={busy} onClick={() => void run()}>
          {busy ? t('admin.backup.running') : t('admin.backup.download')}
        </Button>
        {error && <p role="alert" className="text-sm text-destructive-text">{error}</p>}
        {done && <p role="status" className="text-sm">{t('admin.backup.done', { filename: done.filename, size: (done.bytes / 1024 / 1024).toFixed(2) })}</p>}
      </div>
    </>
  );
}
