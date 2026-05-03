import { describe, it, expect } from 'vitest';
import { getEChartsThemeName } from '../echartsThemes';
import type { ThemeName } from '../../hooks/useTheme';

describe('getEChartsThemeName', () => {
  const themes: ThemeName[] = ['classic', 'claude', 'linear'];

  it.each(themes)('%s + light → %s-light', (theme) => {
    expect(getEChartsThemeName(theme, false)).toBe(`${theme}-light`);
  });

  it.each(themes)('%s + dark → %s-dark', (theme) => {
    expect(getEChartsThemeName(theme, true)).toBe(`${theme}-dark`);
  });

  it('covers all 6 combinations', () => {
    const results = new Set<string>();
    for (const theme of themes) {
      results.add(getEChartsThemeName(theme, true));
      results.add(getEChartsThemeName(theme, false));
    }
    expect(results.size).toBe(6);
  });
});
