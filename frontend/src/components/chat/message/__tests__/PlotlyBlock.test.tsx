/**
 * PlotlyBlock 视觉/工具栏定制单元测试
 *
 * 覆盖:
 * - 移除 plotly logo (displaylogo=false)
 * - 工具栏精简(只显示常用按钮)
 * - 中文字体注入到 template
 * - 商业感配色 colorway
 * - LLM 给的 layout 覆盖 template 默认(title 等可自定义)
 * - LLM 给的 config 不能覆盖强制配置(displaylogo)
 */

import { describe, it, expect } from 'vitest';
import { PROFESSIONAL_TEMPLATE, TOOLBAR_CONFIG, CHART_HEIGHT_PX, stripSizeFields } from '../PlotlyBlock';

describe('PlotlyBlock - 视觉模板', () => {
  describe('PROFESSIONAL_TEMPLATE', () => {
    it('中文字体放在 family 第一位(避免英文 fallback 出现方块)', () => {
      const family = (PROFESSIONAL_TEMPLATE.layout.font as { family: string }).family;
      expect(family).toContain('PingFang SC');
      expect(family).toContain('Microsoft YaHei');
      // 苹方应该在英文字体之前
      expect(family.indexOf('PingFang SC')).toBeLessThan(family.indexOf('sans-serif'));
    });

    it('白底无 chartjunk(paper_bgcolor + plot_bgcolor 都 white)', () => {
      expect(PROFESSIONAL_TEMPLATE.layout.paper_bgcolor).toBe('white');
      expect(PROFESSIONAL_TEMPLATE.layout.plot_bgcolor).toBe('white');
    });

    it('商业感配色(8 色 colorway,蓝色打头)', () => {
      const colorway = PROFESSIONAL_TEMPLATE.layout.colorway as string[];
      expect(colorway).toHaveLength(8);
      expect(colorway[0]).toBe('#3b82f6');  // 商业蓝
    });

    it('网格线浅灰(避免抢眼)', () => {
      const xaxis = PROFESSIONAL_TEMPLATE.layout.xaxis as { gridcolor: string };
      const yaxis = PROFESSIONAL_TEMPLATE.layout.yaxis as { gridcolor: string };
      expect(xaxis.gridcolor).toBe('#f3f4f6');
      expect(yaxis.gridcolor).toBe('#f3f4f6');
    });

    it('automargin 开启(防长中文标签溢出)', () => {
      const xaxis = PROFESSIONAL_TEMPLATE.layout.xaxis as { automargin: boolean };
      const yaxis = PROFESSIONAL_TEMPLATE.layout.yaxis as { automargin: boolean };
      expect(xaxis.automargin).toBe(true);
      expect(yaxis.automargin).toBe(true);
    });

    it('hoverlabel 也用中文字体', () => {
      const hover = PROFESSIONAL_TEMPLATE.layout.hoverlabel as { font: { family: string } };
      expect(hover.font.family).toContain('PingFang SC');
    });

    it('title 左对齐(plotly 官网 demo 风格)', () => {
      const title = PROFESSIONAL_TEMPLATE.layout.title as { x: number; xanchor: string };
      expect(title.x).toBe(0.05);
      expect(title.xanchor).toBe('left');
    });
  });

  describe('TOOLBAR_CONFIG - 移除 plotly 跳转', () => {
    it('displaylogo 关闭(移除"Made with Plotly"角标)', () => {
      expect(TOOLBAR_CONFIG.displaylogo).toBe(false);
    });

    it('删除 sendDataToCloud 按钮(防跳转 plotly 官网)', () => {
      const removed = TOOLBAR_CONFIG.modeBarButtonsToRemove as string[];
      expect(removed).toContain('sendDataToCloud');
    });

    it('删除 editInChartStudio 按钮(防跳转 plotly studio)', () => {
      const removed = TOOLBAR_CONFIG.modeBarButtonsToRemove as string[];
      expect(removed).toContain('editInChartStudio');
    });
  });

  describe('TOOLBAR_CONFIG - 工具栏精简', () => {
    it('工具栏默认隐藏,hover 才显示', () => {
      expect(TOOLBAR_CONFIG.displayModeBar).toBe('hover');
    });

    it('删除冗余按钮(lasso/select/autoScale/spikelines/hover compare)', () => {
      const removed = TOOLBAR_CONFIG.modeBarButtonsToRemove as string[];
      expect(removed).toEqual(
        expect.arrayContaining([
          'lasso2d', 'select2d', 'autoScale2d',
          'hoverClosestCartesian', 'hoverCompareCartesian',
          'toggleSpikelines',
        ]),
      );
    });

    it('保存图片中文文件名 + 2x 高清', () => {
      const opts = TOOLBAR_CONFIG.toImageButtonOptions as {
        filename: string; scale: number; format: string;
      };
      expect(opts.filename).toBe('图表');
      expect(opts.scale).toBe(2);
      expect(opts.format).toBe('png');
    });

    it('locale=zh-CN(工具栏中文 tooltip)', () => {
      expect(TOOLBAR_CONFIG.locale).toBe('zh-CN');
    });

    it('responsive 开启(容器变化自动 resize)', () => {
      expect(TOOLBAR_CONFIG.responsive).toBe(true);
    });
  });

  describe('合并策略(template 与 LLM layout)', () => {
    it('template 机制:LLM 没指定的字段走 template 默认', () => {
      // 模拟 PlotlyBlock 内的合并逻辑
      const llmLayout = { title: { text: 'LLM 自定义标题' } };
      const merged = { template: PROFESSIONAL_TEMPLATE, ...llmLayout };
      expect((merged as { template: typeof PROFESSIONAL_TEMPLATE }).template).toBe(PROFESSIONAL_TEMPLATE);
      expect((merged as { title: { text: string } }).title.text).toBe('LLM 自定义标题');
    });

    it('强制 config 覆盖 LLM 给的 displaylogo(防 LLM 故意打开)', () => {
      // 模拟 PlotlyBlock 内的 config 合并
      const llmConfig = { displaylogo: true };  // LLM 想打开 logo
      const merged = { ...llmConfig, ...TOOLBAR_CONFIG };
      expect(merged.displaylogo).toBe(false);  // 我们强制覆盖
    });
  });

  // ============================================================
  // 大厂自适应策略 (Databricks/Hex/Streamlit/Jupyter):
  //   宽度 100% 容器, 高度固定 450, overflow:hidden 兜底
  // ============================================================

  describe('尺寸控制(大厂"宽度自适应+高度固定"策略)', () => {
    it('图表高度固定 450px(对话场景标准,防图表吞噬上下文)', () => {
      expect(CHART_HEIGHT_PX).toBe(450);
    });
  });

  describe('stripSizeFields(剥离 LLM 给的尺寸字段)', () => {
    it('剥离 width', () => {
      const cleaned = stripSizeFields({ width: 1200, title: 'x' });
      expect(cleaned.width).toBeUndefined();
      expect(cleaned.title).toBe('x');
    });

    it('剥离 height(LLM 经常设 height:800 撑爆容器)', () => {
      const cleaned = stripSizeFields({ height: 800, title: 'x' });
      expect(cleaned.height).toBeUndefined();
      expect(cleaned.title).toBe('x');
    });

    it('剥离 autosize(防 LLM 设 false 锁死尺寸)', () => {
      const cleaned = stripSizeFields({ autosize: false, title: 'x' });
      expect(cleaned.autosize).toBeUndefined();
      expect(cleaned.title).toBe('x');
    });

    it('剥离 margin(防 LLM 设大 margin 让图表视觉撑大)', () => {
      const cleaned = stripSizeFields({ margin: { t: 200, b: 300 }, title: 'x' });
      expect(cleaned.margin).toBeUndefined();
      expect(cleaned.title).toBe('x');
    });

    it('保留所有非尺寸字段(title/xaxis/yaxis/colorway 等)', () => {
      const cleaned = stripSizeFields({
        width: 1200,
        height: 800,
        autosize: false,
        margin: { t: 100 },
        title: { text: '标题' },
        xaxis: { title: 'x 轴' },
        yaxis: { title: 'y 轴' },
        colorway: ['#000'],
        showlegend: true,
      });
      expect(cleaned.title).toEqual({ text: '标题' });
      expect(cleaned.xaxis).toEqual({ title: 'x 轴' });
      expect(cleaned.yaxis).toEqual({ title: 'y 轴' });
      expect(cleaned.colorway).toEqual(['#000']);
      expect(cleaned.showlegend).toBe(true);
    });

    it('空 layout 返回空对象(不崩)', () => {
      expect(stripSizeFields({})).toEqual({});
    });

    it('不修改原对象(返回新对象,避免污染 LLM payload)', () => {
      const original = { width: 1200, title: 'x' };
      const cleaned = stripSizeFields(original);
      expect(original.width).toBe(1200);  // 原对象不变
      expect(cleaned).not.toBe(original);   // 新对象
    });
  });
});
