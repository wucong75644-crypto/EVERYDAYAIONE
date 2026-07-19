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
  vi.doUnmock('../echartsRuntime');
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
    vi.doMock('../echartsRuntime', () => {
      throw new Error('echarts chunk unavailable');
    });
    vi.doMock('../../../../constants/echartsThemes', () => ({
      getEChartsThemeName: vi.fn(() => 'light'),
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

    vi.doUnmock('../echartsRuntime');
    vi.doMock('../echartsRuntime', () => ({
      init: vi.fn(() => chartInstance),
    }));
    fireEvent.click(screen.getByRole('button', { name: '重新渲染' }));

    await waitFor(() => {
      expect(chartInstance.setOption).toHaveBeenCalledTimes(1);
    });
  });
});
