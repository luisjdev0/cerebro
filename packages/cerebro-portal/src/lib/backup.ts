import { ApiError, apiUrl, loadSession } from './api';

/**
 * Downloads a full `pg_dump` of the instance from `POST /auth/backup` (admin only). The dump is
 * buffered as a Blob in the browser, so very large instances are better served by `cerebro backup`.
 */
export async function downloadBackup(): Promise<{ filename: string; bytes: number }> {
  const session = loadSession();
  let res: Response;
  try {
    res = await fetch(apiUrl('/auth/backup'), { method: 'POST', headers: session ? { Authorization: `Bearer ${session.token}` } : {} });
  } catch {
    throw new ApiError(0, 'Could not reach the server');
  }
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const d = (await res.json())?.detail;
      if (typeof d === 'string') detail = d;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  const blob = await res.blob();
  const m = /filename="?([^";]+)"?/.exec(res.headers.get('Content-Disposition') ?? '');
  const filename = m?.[1] ?? `cerebro-backup-${new Date().toISOString().replace(/[-:]/g, '').slice(0, 15)}Z.sql`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return { filename, bytes: blob.size };
}
