import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ChartBlock from '../ChartBlock';

const chartInstance = {
  setOption: vi.fn(),
  dispose: vi.fn(),
  resize: vi.fn(),
  getDataURL: vi.fn(() => 'data:image/png;base64,test'),
};
const loggerErrorMock = vi.fn();

vi.mock('echarts/core', () => ({
  use: vi.fn(),
  init: vi.fn(() => chartInstance),
  registerTheme: vi.fn(),
}));
vi.mock('echarts/renderers', () => ({ CanvasRenderer: {} }));
vi.mock('echarts/charts', () => ({
  LineChart: {}, BarChart: {}, PieChart: {}, ScatterChart: {}, RadarChart: {},
  HeatmapChart: {}, FunnelChart: {}, BoxplotChart: {}, TreemapChart: {},
  SunburstChart: {}, SankeyChart: {}, GaugeChart: {}, CandlestickChart: {},
}));
vi.mock('echarts/components', () => ({
  GridComponent: {}, TooltipComponent: {}, LegendComponent: {}, ToolboxComponent: {},
  DataZoomComponent: {}, TitleComponent: {}, VisualMapComponent: {}, MarkLineComponent: {},
  MarkPointComponent: {}, DatasetComponent: {},
}));
vi.mock('../../../../constants/echartsThemes', () => ({
  getEChartsThemeName: vi.fn(() => 'light'),
  registerAllThemes: vi.fn(),
}));
vi.mock('../../../../hooks/useTheme', () => ({
  useTheme: () => ({ theme: 'classic', isDark: false }),
}));
vi.mock('../../../../utils/logger', () => ({
  logger: { error: loggerErrorMock, info: vi.fn() },
}));
vi.mock('../PlotlyBlock', () => ({ default: () => <div>Plotly renderer</div> }));
vi.mock('../VegaLiteBlock', () => ({ default: () => <div>Vega renderer</div> }));

describe('ChartBlock data view', () => {
  beforeEach(() => {
    chartInstance.setOption.mockReset();
    chartInstance.setOption.mockImplementation(() => undefined);
    loggerErrorMock.mockReset();
  });

  it('routes explicit chart formats to their renderers', async () => {
    const { rerender } = render(<ChartBlock option={{}} spec_format="plotly" />);
    expect(await screen.findByText('Plotly renderer')).toBeInTheDocument();
    rerender(<ChartBlock option={{}} spec_format="vegalite" />);
    expect(await screen.findByText('Vega renderer')).toBeInTheDocument();
  });

  it('falls back to JSON for unknown chart formats', () => {
    render(<ChartBlock option={{ value: 42 }} spec_format="unknown" />);

    expect(screen.getByText(/不支持的图表格式/)).toBeInTheDocument();
    expect(screen.getByText(/\"value\": 42/)).toBeInTheDocument();
  });

  it('does not initialize ECharts for an empty option', async () => {
    render(<ChartBlock option={{}} />);

    expect(await screen.findByText('图表配置为空')).toBeInTheDocument();
    expect(screen.getByText('{}')).toBeInTheDocument();
  });

  it('restores the renderer container before retrying a failed render', async () => {
    chartInstance.setOption.mockImplementationOnce(() => {
      throw new Error('sensitive-customer-series');
    });
    render(<ChartBlock messageId="message-42" option={{
      xAxis: { data: ['A'] },
      series: [{ type: 'line', data: [1] }],
    }} />);

    expect(await screen.findByText('sensitive-customer-series')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重新渲染' }));

    await waitFor(() => {
      expect(screen.getByTitle('数据视图')).toBeInTheDocument();
    });
    expect(chartInstance.setOption).toHaveBeenCalledTimes(2);
    expect(loggerErrorMock).toHaveBeenCalledWith(
      'chart:render',
      'ECharts render failed',
      undefined,
      {
        messageId: 'message-42',
        contentType: 'chart',
        renderer: 'echarts',
        errorType: 'Error',
      },
    );
    expect(JSON.stringify(loggerErrorMock.mock.calls)).not.toContain(
      'sensitive-customer-series',
    );
  });

  it('renders structured series values without passing objects to React', async () => {
    render(
      <ChartBlock option={{
        xAxis: { data: ['A'] },
        series: [{ type: 'bar', name: '结果', data: [{ answer: 42 }] }],
      }} />,
    );

    await waitFor(() => expect(screen.getByTitle('数据视图')).toBeInTheDocument());
    fireEvent.click(screen.getByTitle('数据视图'));

    expect(screen.getByText('{"answer":42}')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('[object Object]');
  });
});
