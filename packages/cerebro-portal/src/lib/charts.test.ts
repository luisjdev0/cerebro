import { describe, expect, it } from 'vitest';
import { monthlySeries, topSlices } from './charts';

describe('monthlySeries', () => {
  const now = new Date(Date.UTC(2026, 10, 8)); // 8 Nov 2026
  it('returns 12 consecutive months ending in the current one, zero-filled', () => {
    const s = monthlySeries([], now);
    expect(s).toHaveLength(12);
    expect(s[0].month).toBe('2025-12');
    expect(s[11].month).toBe('2026-11');
    expect(s.every((p) => p.created === 0 && p.updated === 0)).toBe(true);
  });
  it('sums days into their month and ignores days outside the window', () => {
    const s = monthlySeries(
      [
        { date: '2026-11-01', created: 2, updated: 1 },
        { date: '2026-11-07', created: 1, updated: 0 },
        { date: '2025-01-10', created: 9, updated: 9 },
      ],
      now,
    );
    expect(s[11]).toEqual({ month: '2026-11', created: 3, updated: 1 });
    expect(s.reduce((a, p) => a + p.created, 0)).toBe(3);
  });
});

describe('topSlices', () => {
  const items = ['a', 'b', 'c', 'd', 'e', 'f', 'g'].map((k, i) => ({ key: k, name: k, value: 10 - i }));
  it('folds the tail into one "other" slice', () => {
    const s = topSlices(items, 5);
    expect(s).toHaveLength(5);
    expect(s[4]).toEqual({ key: '__other', name: 'Other', value: 6 + 5 + 4 });
  });
  it('keeps everything when it fits and drops zero-sized slices', () => {
    expect(topSlices([{ key: 'x', name: 'x', value: 0 }, { key: 'y', name: 'y', value: 2 }])).toEqual([{ key: 'y', name: 'y', value: 2 }]);
  });
});
