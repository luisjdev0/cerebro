import { describe, expect, it } from 'vitest';
import { mermaidBlob, plainSource, svgSize, withPixelSize } from './diagramExport';

const MERMAID_SVG = '<svg id="x" width="100%" xmlns="http://www.w3.org/2000/svg" style="max-width: 1104.5px;" viewBox="0 0 1104.54 8812.125" role="graphics-document"><g/></svg>';

describe('diagram export helpers', () => {
  it('reads the natural size from the viewBox (rounded up)', () => {
    expect(svgSize(MERMAID_SVG)).toEqual({ w: 1105, h: 8813 });
    expect(svgSize('<svg></svg>')).toEqual({ w: 800, h: 600 });
  });

  it('replaces width="100%"/max-width with pixel dimensions and an optional background', () => {
    const out = withPixelSize(MERMAID_SVG, { w: 1105, h: 8813 }, '#ffffff');
    expect(out).toContain('width="1105" height="8813"');
    expect(out).toContain('style="background:#ffffff"');
    expect(out).not.toContain('100%');
    expect(out).not.toContain('max-width');
    expect(out).toContain('viewBox="0 0 1104.54 8812.125"');
  });

  it('adds the SVG namespace when it is missing', () => {
    expect(withPixelSize('<svg viewBox="0 0 1 1"></svg>', { w: 1, h: 1 })).toContain('xmlns="http://www.w3.org/2000/svg"');
  });

  it('strips bold tags, which plain-text labels cannot render', () => {
    expect(plainSource('a["<b>x</b><br/>y"]')).toBe('a["x<br/>y"]');
  });
});

describe('mermaidBlob', () => {
  it('keeps the source verbatim as plain text and ends it with a newline', async () => {
    const src = 'flowchart TD\n  A["<b>Start</b>"] --> B';
    const blob = mermaidBlob(src);
    expect(blob.type).toBe('text/plain;charset=utf-8');
    expect(await new Response(blob).text()).toBe(`${src}\n`);
    expect(await new Response(mermaidBlob(`${src}\n`)).text()).toBe(`${src}\n`);
  });
});
