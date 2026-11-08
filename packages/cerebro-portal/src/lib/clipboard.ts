/** Copies text to the clipboard. `navigator.clipboard` only exists in secure contexts (https or
 *  localhost), and the portal is also served over plain http on a LAN, so fall back to execCommand. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to the legacy path */
  }
  // A modal panel traps focus, so the helper textarea must live inside it (next to the clicked
  // button) or the selection is lost before the copy runs.
  const previous = document.activeElement;
  const host = (document.activeElement as HTMLElement | null)?.parentElement ?? document.body;
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0;';
  host.appendChild(ta);
  ta.focus();
  ta.select();
  try {
    return document.execCommand('copy');
  } catch {
    return false;
  } finally {
    host.removeChild(ta);
    (previous as HTMLElement | null)?.focus?.();
  }
}
