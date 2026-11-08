import { describe, expect, it } from 'vitest';
import { plainExcerpt } from './documents';

describe('plainExcerpt', () => {
  it('strips markdown noise into one line', () => {
    expect(plainExcerpt('# Title\n\nSome **bold** text and a [link](http://x.y).\n\n- item')).toBe('Title Some bold text and a link. item');
  });
  it('drops fenced code blocks', () => {
    expect(plainExcerpt('before\n```js\nconst a = 1;\n```\nafter')).toBe('before after');
  });
});
