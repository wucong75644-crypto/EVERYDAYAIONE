/**
 * ECharts 主题配置
 *
 * 为 3 × 2 = 6 种主题/明暗组合定义 ECharts 主题色板。
 * 色值从 theme.css 中的 design token 提取，保持视觉一致。
 *
 * 使用方式：
 *   import { getEChartsThemeName, registerAllThemes } from './echartsThemes';
 *   registerAllThemes();  // 启动时注册一次
 *   echarts.init(dom, getEChartsThemeName('classic', true));
 */

import type { ThemeName } from '../hooks/useTheme';

/** ECharts 主题配置结构（仅覆盖需要自定义的部分） */
interface EChartsThemeConfig {
  color: string[];                 // 系列色板
  backgroundColor: string;
  textStyle: { color: string };
  title: { textStyle: { color: string }; subtextStyle: { color: string } };
  legend: { textStyle: { color: string } };
  categoryAxis: { axisLine: { lineStyle: { color: string } }; axisTick: { lineStyle: { color: string } }; axisLabel: { color: string }; splitLine: { lineStyle: { color: string } } };
  valueAxis: { axisLine: { lineStyle: { color: string } }; axisTick: { lineStyle: { color: string } }; axisLabel: { color: string }; splitLine: { lineStyle: { color: string } } };
  tooltip: { backgroundColor: string; borderColor: string; textStyle: { color: string } };
  toolbox: { iconStyle: { borderColor: string } };
}

// ============================================================
// 色板定义
// ============================================================

/** 经典蓝色板（对齐 classic 主题的 blue-600 品牌色） */
const CLASSIC_PALETTE = [
  '#2563eb', '#f59e0b', '#10b981', '#ef4444',
  '#8b5cf6', '#ec4899', '#06b6d4', '#f97316',
];

/** Claude 暖色板（对齐 claude 主题的 amber-700 品牌色） */
const CLAUDE_PALETTE = [
  '#b45309', '#0d9488', '#7c3aed', '#e11d48',
  '#2563eb', '#ca8a04', '#059669', '#dc2626',
];

/** Linear 工程色板（对齐 linear 主题的极简高对比） */
const LINEAR_PALETTE = [
  '#818cf8', '#34d399', '#fbbf24', '#f87171',
  '#a78bfa', '#38bdf8', '#fb923c', '#4ade80',
];

// ============================================================
// 主题工厂
// ============================================================

function buildTheme(
  palette: string[],
  bg: string,
  textPrimary: string,
  textSecondary: string,
  borderColor: string,
  splitColor: string,
  tooltipBg: string,
  tooltipBorder: string,
): EChartsThemeConfig {
  const axisCommon = {
    axisLine: { lineStyle: { color: borderColor } },
    axisTick: { lineStyle: { color: borderColor } },
    axisLabel: { color: textSecondary },
    splitLine: { lineStyle: { color: splitColor } },
  };
  return {
    color: palette,
    backgroundColor: bg,
    textStyle: { color: textPrimary },
    title: {
      textStyle: { color: textPrimary },
      subtextStyle: { color: textSecondary },
    },
    legend: { textStyle: { color: textSecondary } },
    categoryAxis: axisCommon,
    valueAxis: axisCommon,
    tooltip: {
      backgroundColor: tooltipBg,
      borderColor: tooltipBorder,
      textStyle: { color: textPrimary },
    },
    toolbox: { iconStyle: { borderColor: textSecondary } },
  };
}

// ============================================================
// 6 套主题
// ============================================================

const THEMES: Record<string, EChartsThemeConfig> = {
  // Classic Light
  'classic-light': buildTheme(
    CLASSIC_PALETTE,
    '#ffffff', '#111827', '#6b7280',
    '#e5e7eb', '#f3f4f6',
    '#ffffff', '#e5e7eb',
  ),
  // Classic Dark
  'classic-dark': buildTheme(
    CLASSIC_PALETTE,
    '#111827', '#f9fafb', '#9ca3af',
    '#374151', '#1f2937',
    '#1f2937', '#374151',
  ),
  // Claude Light
  'claude-light': buildTheme(
    CLAUDE_PALETTE,
    '#fdfbf7', '#292524', '#78716c',
    '#e7e5e4', '#f5f5f4',
    '#fdfbf7', '#e7e5e4',
  ),
  // Claude Dark
  'claude-dark': buildTheme(
    CLAUDE_PALETTE,
    '#1c1917', '#fafaf9', '#a8a29e',
    '#44403c', '#292524',
    '#292524', '#44403c',
  ),
  // Linear Light（极少使用，Linear 默认 dark-first）
  'linear-light': buildTheme(
    LINEAR_PALETTE,
    '#fafafa', '#18181b', '#71717a',
    '#e4e4e7', '#f4f4f5',
    '#ffffff', '#e4e4e7',
  ),
  // Linear Dark
  'linear-dark': buildTheme(
    LINEAR_PALETTE,
    '#09090b', '#fafafa', '#a1a1aa',
    '#27272a', '#18181b',
    '#18181b', '#27272a',
  ),
};

// ============================================================
// 公开 API
// ============================================================

/**
 * 注册所有 ECharts 主题（应用启动时调用一次）
 *
 * 延迟导入 echarts，避免未使用图表时加载整个库。
 */
export async function registerAllThemes(): Promise<void> {
  const echarts = await import('echarts/core');
  for (const [name, config] of Object.entries(THEMES)) {
    echarts.registerTheme(name, config);
  }
}

/** 根据当前主题+明暗获取 ECharts 主题名 */
export function getEChartsThemeName(theme: ThemeName, isDark: boolean): string {
  return `${theme}-${isDark ? 'dark' : 'light'}`;
}
