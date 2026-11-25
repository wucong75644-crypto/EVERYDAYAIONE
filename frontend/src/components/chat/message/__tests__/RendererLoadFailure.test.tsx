import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const { chartInstance } = vi.hoisted(() => ({
  chartInstance: {
    setOption: vi.fn(),
    dispose: vi.fn(),
    resize: vi.fn(),
    getDataURL: vi.fn(),
  },
}));

afterEach(() => {
  cleanup();
  vi.doUnmock('mermaid');
  vi.doUnmock('echarts/core');
  vi.doUnmock('echarts/renderers');
  vi.doUnmock('echarts/charts');
  vi.doUnmock('echarts/components');
  vi.resetModules();
});

describe('renderer chunk loading retries', () => {
  it('retries Mermaid after its dynamic import fails', async () => {
    vi.doMock('mermaid', () => {
      throw new Error('mermaid chunk unavailable');
    });
    vi.doMock('../../../../utils/logger', () => ({
      logger: { error: vi.fn() },
    }));
    const { default: MermaidRenderer } = await import('../MermaidRenderer');

    render(<MermaidRenderer source="flowchart TD\nA-->B" />);
    expect(await screen.findByText(/关系图渲染失败/)).toBeInTheDocument();

    vi.doUnmock('mermaid');
    vi.doMock('mermaid', () => ({
      default: {
        initialize: vi.fn(),
        render: vi.fn().mockResolvedValue({
          svg: '<svg><text>mermaid ready</text></svg>',
        }),
      },
    }));
    fireEvent.click(screen.getByRole('button', { name: '重新渲染' }));

    await waitFor(() => {
      expect(screen.getByTestId('mermaid-svg')).toHaveTextContent('mermaid ready');
    });
  });

  it('retries ECharts after its dynamic import fails', async () => {
    chartInstance.setOption.mockReset();
    let coreUnavailable = true;
    const register = vi.fn();
    // Keep one module factory for both attempts. Swapping unmock/mock while
    // Promise.all imports are pending can mix real core with fake extensions.
    vi.doMock('echarts/core', () => {
      if (coreUnavailable) throw new Error('echarts chunk unavailable');
      return { use: register, init: vi.fn(() => chartInstance), registerTheme: vi.fn() };
    });
    vi.doMock('echarts/renderers', () => ({ CanvasRenderer: {} }));
    vi.doMock('echarts/charts', () => ({
      LineChart: {}, BarChart: {}, PieChart: {}, ScatterChart: {}, RadarChart: {},
      HeatmapChart: {}, FunnelChart: {}, BoxplotChart: {}, TreemapChart: {},
      SunburstChart: {}, SankeyChart: {}, GaugeChart: {}, CandlestickChart: {},
    }));
    vi.doMock('echarts/components', () => ({
      GridComponent: {}, TooltipComponent: {}, LegendComponent: {},
      ToolboxComponent: {}, DataZoomComponent: {}, TitleComponent: {},
      VisualMapComponent: {}, MarkLineComponent: {}, MarkPointComponent: {},
      DatasetComponent: {},
    }));
    vi.doMock('../../../../constants/echartsThemes', () => ({
      getEChartsThemeName: vi.fn(() => 'light'),
      registerAllThemes: vi.fn(),
    }));
    vi.doMock('../../../../hooks/useTheme', () => ({
      useTheme: () => ({ theme: 'classic', isDark: false }),
    }));
    vi.doMock('../../../../utils/logger', () => ({
      logger: { error: vi.fn(), info: vi.fn() },
    }));
    const { default: EChartsRenderer } = await import('../EChartsRenderer');

    render(<EChartsRenderer option={{ series: [{ type: 'bar', data: [1] }] }} />);
    expect(await screen.findByText('图表渲染失败')).toBeInTheDocument();

    coreUnavailable = false;
    // Invalidate Vitest's failed factory cache; the mounted production hook
    // must still clear its own rejected loader promise for retry to work.
    vi.resetModules();
    fireEvent.click(screen.getByRole('button', { name: '重新渲染' }));

    await waitFor(() => {
      expect(register).toHaveBeenCalledTimes(1);
      expect(chartInstance.setOption).toHaveBeenCalledTimes(1);
    });
  });
});
