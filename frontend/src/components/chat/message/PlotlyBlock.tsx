/**
 * Plotly 图表渲染块
 *
 * 由 ChartBlock 在 spec_format='plotly' 时分发,
 * 走 plotly.js-basic-dist-min (按需加载,~200KB gzip,比 full dist 小 5 倍)。
 *
 * basic 版支持: scatter / bar / pie / heatmap / contour / histogram / box /
 *                candlestick / scatterpolar 等基础图表,覆盖 LLM 99% 数据可视化场景。
 * 不支持: 3D / 地图 / scatter3d / mesh3d 等高级类型(LLM 极少用)。
 *
 * 视觉:对齐 plotly 官网 demo 风格 + 中文字体 + 简洁工具栏(移除 plotly logo)。
 * 详见 docs/document/TECH_沙盒产物协议.md
 */
import { useEffect, useMemo, useRef, useState, memo } from 'react';
import { logger } from '../../../utils/logger';

interface PlotlyBlockProps {
  option: Record<string, unknown>;
  // title 由 plotly 内部 layout.title 渲染(PROFESSIONAL_TEMPLATE 已配 16px 左对齐),
  // 不再外层重复渲染,避免与 plotly 内部 title 双标题(对齐 ChartBlock 处理)。
  // 保留 prop 仅供未来扩展(如 fullscreen 模式可加),默认不显示。
  title?: string;
}

let plotlyPromise: Promise<typeof import('plotly.js-basic-dist-min')> | null = null;
function getPlotly() {
  if (!plotlyPromise) {
    plotlyPromise = import('plotly.js-basic-dist-min');
  }
  return plotlyPromise;
}

// ============================================================
// 专业视觉模板(对齐 plotly 官网 demo + 中文场景优化)
// ============================================================
//
// plotly template 机制:LLM 给的 layout 字段覆盖 template 默认值,
// 我们的 template 设默认风格,LLM 仍可自定义 title/标签/特殊样式。

const PROFESSIONAL_TEMPLATE = {
  layout: {
    font: {
      family: '"PingFang SC", "Microsoft YaHei", "Hiragino Sans GB", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
      size: 13,
      color: '#1f2937',
    },
    paper_bgcolor: 'white',
    plot_bgcolor: 'white',
    // 现代商业感配色(蓝/绿/橙/红/紫/粉/青/草绿)
    colorway: [
      '#3b82f6', '#10b981', '#f59e0b', '#ef4444',
      '#8b5cf6', '#ec4899', '#06b6d4', '#84cc16',
    ],
    // legend 改水平放底部(默认右侧垂直会挤压绘图区宽度),底边距 b:80 给 legend 留位置
    margin: { l: 60, r: 30, t: 60, b: 80, pad: 4 },
    xaxis: {
      gridcolor: '#f3f4f6',
      linecolor: '#e5e7eb',
      zerolinecolor: '#e5e7eb',
      tickfont: { size: 12, color: '#4b5563' },
      automargin: true,
      showline: true,
    },
    yaxis: {
      gridcolor: '#f3f4f6',
      linecolor: '#e5e7eb',
      zerolinecolor: '#e5e7eb',
      tickfont: { size: 12, color: '#4b5563' },
      automargin: true,
      showline: true,
    },
    title: {
      font: { size: 16, color: '#111827', weight: 600 },
      x: 0.05,
      xanchor: 'left',
      y: 0.95,
    },
    hoverlabel: {
      font: { family: '"PingFang SC", "Microsoft YaHei", sans-serif', size: 12 },
      bgcolor: 'white',
      bordercolor: '#e5e7eb',
    },
    // legend 水平放底部:默认右侧垂直会挤压绘图区宽度(用户实测窄了一半)
    legend: {
      orientation: 'h',
      y: -0.18,
      x: 0.5,
      xanchor: 'center',
      yanchor: 'top',
      font: { size: 12, color: '#4b5563' },
      bgcolor: 'rgba(255,255,255,0)',
    },
    bargap: 0.3,
  },
};

// ============================================================
// 工具栏配置(精简实用,移除 plotly logo / cloud 跳转)
// ============================================================

const TOOLBAR_CONFIG: Record<string, unknown> = {
  // 关键:移除右下角 "Made with Plotly" 跳转角标
  displaylogo: false,
  // 鼠标 hover 在图表上才显示工具栏(默认常驻太占视觉)
  displayModeBar: 'hover',
  // 删除冗余 / 高级按钮(只保留常用 5 个:保存/缩放/平移/重置/全屏)
  modeBarButtonsToRemove: [
    'lasso2d',                // 套索选择(数据分析少用)
    'select2d',               // 矩形选区(同上)
    'autoScale2d',            // 自适应缩放(跟重置重复)
    'hoverClosestCartesian',  // 悬浮模式切换
    'hoverCompareCartesian',  // 悬浮对比模式
    'toggleSpikelines',       // 十字辅助线
    'sendDataToCloud',        // 发送到 plotly cloud(跳转外站,删)
    'editInChartStudio',      // 在 plotly cloud 编辑(跳转外站,删)
  ],
  // 保存图片:中文文件名 + 高清 + 默认尺寸
  toImageButtonOptions: {
    filename: '图表',
    format: 'png',
    height: 600,
    width: 1000,
    scale: 2,
  },
  responsive: true,
  locale: 'zh-CN',
};

// ============================================================
// 主组件
// ============================================================

function PlotlyBlockInner({ option }: PlotlyBlockProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // 合并:LLM layout 覆盖 template 默认 + 强制 config
  const { data, mergedLayout, mergedConfig } = useMemo(() => {
    const rawData = (option.data as unknown[]) || [];
    const rawLayout = (option.layout as Record<string, unknown>) || {};
    const rawConfig = (option.config as Record<string, unknown>) || {};
    return {
      data: rawData,
      // template 机制:LLM 的 layout 覆盖 template 同字段, 没指定的走 template
      mergedLayout: {
        template: PROFESSIONAL_TEMPLATE,
        ...rawLayout,
      },
      // config:LLM 给的 + 我们强制的(强制覆盖 displaylogo 等)
      mergedConfig: {
        ...rawConfig,
        ...TOOLBAR_CONFIG,
      },
    };
  }, [option]);

  useEffect(() => {
    if (!containerRef.current) return;
    let disposed = false;
    (async () => {
      try {
        const Plotly = await getPlotly();
        if (disposed || !containerRef.current) return;
        await Plotly.newPlot(containerRef.current, data as never, mergedLayout, mergedConfig);
        setLoading(false);
        setError(null);
      } catch (e) {
        if (!disposed) {
          logger.error('PlotlyBlock', `init failed | ${e}`);
          setError(`Plotly 渲染失败: ${e instanceof Error ? e.message : String(e)}`);
          setLoading(false);
        }
      }
    })();
    return () => {
      disposed = true;
      if (containerRef.current) {
        getPlotly().then((Plotly) => {
          try { Plotly.purge(containerRef.current!); } catch { /* ignore */ }
        }).catch(() => { /* ignore */ });
      }
    };
  }, [data, mergedLayout, mergedConfig]);

  // ResizeObserver: 容器宽度变化时(窗口 resize / 侧边栏切换)触发 plotly 重新计算
  // 否则 plotly 渲染时的宽度被锁死,不会响应父级宽度变化
  useEffect(() => {
    if (!containerRef.current) return;
    const el = containerRef.current;
    let cancelled = false;
    const ro = new ResizeObserver(() => {
      if (cancelled) return;
      getPlotly().then((Plotly) => {
        const PlotlyAny = Plotly as unknown as { Plots?: { resize: (el: HTMLElement) => void } };
        if (!cancelled && el && PlotlyAny.Plots?.resize) {
          try { PlotlyAny.Plots.resize(el); } catch { /* ignore */ }
        }
      }).catch(() => { /* ignore */ });
    });
    ro.observe(el);
    return () => { cancelled = true; ro.disconnect(); };
  }, []);

  if (error) {
    return (
      <div className="my-3 rounded-xl border border-border-default bg-surface-card p-4">
        <div className="text-sm font-medium text-error mb-2">Plotly 图表渲染失败</div>
        <p className="text-xs text-text-tertiary">{error}</p>
      </div>
    );
  }

  return (
    <div className="my-3 relative" style={{ height: 450 }}>
      {/* title 不在外层渲染:plotly 内部 layout.title 已由 PROFESSIONAL_TEMPLATE
          渲染(左对齐 16px 加粗),外层再渲染会出现双标题 + 中间空白 */}

      {/* 容器固定 height:400 始终有尺寸(不用 display:none),plotly newPlot 才能读
          到正确容器尺寸渲染。否则 newPlot 时容器是 0x0,plotly 用默认 450px 撑
          超容器覆盖下方文字。loading 占位用 absolute 覆盖在容器上方,plotly 渲染
          完成自然显现。 */}
      <div ref={containerRef} style={{ width: '100%', height: 450 }} />
      {loading && (
        <div className="absolute inset-0 rounded-xl flex items-center justify-center bg-hover">
          <svg className="w-8 h-8 text-text-disabled animate-pulse" viewBox="0 0 24 24"
               fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M3 3v18h18" /><path d="M7 16l4-8 4 4 4-6" />
          </svg>
        </div>
      )}
    </div>
  );
}

export default memo(PlotlyBlockInner);

// 导出常量供测试使用
export { PROFESSIONAL_TEMPLATE, TOOLBAR_CONFIG };
