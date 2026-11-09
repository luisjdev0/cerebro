// Export of the flow diagram as SVG, PNG or PDF. Exports are always drawn light on white and
// with plain SVG text (no HTML labels): foreignObject content taints a canvas and breaks in
// most SVG viewers, and a printed/shared diagram should not depend on the app's theme.

export type Size = { w: number; h: number };

/** Natural size of a Mermaid SVG, from its viewBox. */
export function svgSize(svg: string): Size {
  const m = /viewBox="\s*(-?[\d.]+)[ ,]+(-?[\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)\s*"/.exec(svg);
  return m ? { w: Math.ceil(Number(m[3])), h: Math.ceil(Number(m[4])) } : { w: 800, h: 600 };
}

/** Gives the root <svg> explicit pixel dimensions (Mermaid emits width="100%" and a max-width style). */
export function withPixelSize(svg: string, { w, h }: Size, background?: string): string {
  return svg.replace(/<svg\b[^>]*>/, (tag) => {
    let t = tag.replace(/\swidth="[^"]*"/, '').replace(/\sheight="[^"]*"/, '').replace(/\sstyle="[^"]*"/, '');
    if (!/xmlns=/.test(t)) t = t.replace('<svg', '<svg xmlns="http://www.w3.org/2000/svg"');
    const style = background ? ` style="background:${background}"` : '';
    return t.replace(/>$/, ` width="${w}" height="${h}"${style}>`);
  });
}

/** The Mermaid source without the HTML bold tags, which plain-text SVG labels cannot render. */
export const plainSource = (source: string) => source.replace(/<\/?b>/g, '');

let n = 0;
/** Renders the source as a standalone light SVG with pixel dimensions. */
export async function renderExportSvg(source: string): Promise<{ svg: string; size: Size }> {
  const { default: mermaid } = await import('mermaid');
  mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'default', htmlLabels: false, flowchart: { htmlLabels: false, curve: 'basis' } });
  const out = await mermaid.render(`flow-export-${++n}`, plainSource(source));
  const size = svgSize(out.svg);
  // Any leftover HTML label must at least be well-formed XML for an <img>/standalone SVG.
  const xml = out.svg.replace(/<br\s*>/g, '<br/>');
  return { svg: withPixelSize(xml, size, '#ffffff'), size };
}

const MAX_SIDE = 16000;

export async function svgToCanvas(svg: string, { w, h }: Size, scale = 2): Promise<HTMLCanvasElement> {
  const s = Math.max(0.1, Math.min(scale, MAX_SIDE / Math.max(w, h)));
  const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml;charset=utf-8' }));
  try {
    const img = new Image();
    img.src = url;
    await img.decode();
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(w * s);
    canvas.height = Math.round(h * s);
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('canvas unavailable');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    return canvas;
  } finally {
    URL.revokeObjectURL(url);
  }
}

export const canvasBlob = (canvas: HTMLCanvasElement, type: string, quality?: number) =>
  new Promise<Blob>((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('export failed'))), type, quality));

/** A one-page PDF whose page is the size of the diagram, holding the diagram as a JPEG image. */
export async function pdfFromCanvas(canvas: HTMLCanvasElement, widthPt: number, heightPt: number): Promise<Blob> {
  const jpeg = new Uint8Array(await (await canvasBlob(canvas, 'image/jpeg', 0.92)).arrayBuffer());
  const enc = new TextEncoder();
  const parts: Uint8Array[] = [];
  const offsets: number[] = [];
  let length = 0;
  const push = (data: Uint8Array | string) => {
    const bytes = typeof data === 'string' ? enc.encode(data) : data;
    parts.push(bytes);
    length += bytes.length;
  };
  const obj = (id: number, body: string) => {
    offsets[id] = length;
    push(`${id} 0 obj\n${body}\nendobj\n`);
  };
  const W = widthPt.toFixed(2);
  const H = heightPt.toFixed(2);
  push('%PDF-1.4\n');
  obj(1, '<< /Type /Catalog /Pages 2 0 R >>');
  obj(2, '<< /Type /Pages /Kids [3 0 R] /Count 1 >>');
  obj(3, `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${W} ${H}] /Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>`);
  offsets[4] = length;
  push(`4 0 obj\n<< /Type /XObject /Subtype /Image /Width ${canvas.width} /Height ${canvas.height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${jpeg.length} >>\nstream\n`);
  push(jpeg);
  push('\nendstream\nendobj\n');
  const content = `q ${W} 0 0 ${H} 0 0 cm /Im0 Do Q`;
  obj(5, `<< /Length ${content.length} >>\nstream\n${content}\nendstream`);
  const xref = length;
  let table = 'xref\n0 6\n0000000000 65535 f \n';
  for (let i = 1; i <= 5; i++) table += `${String(offsets[i]).padStart(10, '0')} 00000 n \n`;
  push(`${table}trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
  return new Blob(parts as BlobPart[], { type: 'application/pdf' });
}

export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
