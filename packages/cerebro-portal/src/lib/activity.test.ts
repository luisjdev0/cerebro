import { describe, expect, it } from 'vitest';
import { buildGrid, buildYearGrid, levelFor, totalsByDate } from './activity';

describe('buildGrid', () => {
  it('produces `weeks` Sunday-first columns of 7 days ending on today', () => {
    const today = new Date(Date.UTC(2026, 10, 8)); // Sunday 2026-11-08
    const grid = buildGrid(today, 53);
    expect(grid).toHaveLength(53);
    expect(grid.every((c) => c.length === 7)).toBe(true);
    expect(new Date(grid[0][0].date + 'T00:00:00Z').getUTCDay()).toBe(0);
    const last = grid[52];
    expect(last[0].date).toBe('2026-11-08');
    expect(last[0].inRange).toBe(true);
    expect(last[1].inRange).toBe(false); // future days in the current week are out of range
  });

  it('keeps a mid-week today inside the last column', () => {
    const grid = buildGrid(new Date(Date.UTC(2026, 10, 11)), 10); // Wednesday
    const inRange = grid[9].filter((d) => d.inRange).map((d) => d.date);
    expect(inRange).toEqual(['2026-11-08', '2026-11-09', '2026-11-10', '2026-11-11']);
  });
});

describe('levelFor', () => {
  it('maps counts to five levels relative to the busiest day', () => {
    expect(levelFor(0, 10)).toBe(0);
    expect(levelFor(1, 10)).toBe(1);
    expect(levelFor(4, 10)).toBe(2);
    expect(levelFor(6, 10)).toBe(3);
    expect(levelFor(10, 10)).toBe(4);
    expect(levelFor(5, 0)).toBe(0);
  });
});

describe('totalsByDate', () => {
  it('sums created+updated across the selected modules only', () => {
    const a = {
      memory: [{ date: '2026-11-01', created: 2, updated: 1 }],
      docs: [{ date: '2026-11-01', created: 1, updated: 0 }],
      flows: [{ date: '2026-11-02', created: 0, updated: 4 }],
    };
    expect(totalsByDate(a, ['memory', 'docs']).get('2026-11-01')).toBe(4);
    expect(totalsByDate(a, ['flows']).get('2026-11-02')).toBe(4);
    expect(totalsByDate(a, ['memory']).has('2026-11-02')).toBe(false);
  });
});

describe('buildYearGrid', () => {
  it('spans the weeks holding Jan 1 and Dec 31 of the year', () => {
    const grid = buildYearGrid(2025, new Date(Date.UTC(2026, 10, 8)));
    expect(grid[0][0].date).toBe('2024-12-29'); // Sunday before Wed 2025-01-01
    expect(grid[0][3].inRange).toBe(true); // 2025-01-01 (a Wednesday)
    expect(grid[0][2].inRange).toBe(false); // 2024-12-31 belongs to the previous year
    const lastCol = grid[grid.length - 1];
    expect(lastCol.some((d) => d.date === '2025-12-31' && d.inRange)).toBe(true);
    expect(lastCol.some((d) => d.date.startsWith('2026') && d.inRange)).toBe(false);
  });
  it('leaves days after today out of range in the current year', () => {
    const grid = buildYearGrid(2026, new Date(Date.UTC(2026, 10, 8)));
    const flat = grid.flat();
    expect(flat.find((d) => d.date === '2026-11-08')?.inRange).toBe(true);
    expect(flat.find((d) => d.date === '2026-11-09')?.inRange).toBe(false);
  });
});
