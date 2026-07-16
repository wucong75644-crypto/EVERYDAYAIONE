import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ChartBlock from '../ChartBlock';

const chartInstance = {
  setOption: vi.fn(),
  dispose: vi.fn(),
  resize: vi.fn(),
  getDataURL: vi.fn(() => 'data:image/png;base64,test'),
};

vi.mock('echarts/core', () => ({
  use: vi.fn(),
  init: vi.fn(() => chartInstance),
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
vi.mock('../PlotlyBlock', () => ({ default: () => <div>Plotly renderer</div> }));
vi.mock('../VegaLiteBlock', () => ({ default: () => <div>Vega renderer</div> }));

describe('ChartBlock data view', () => {
  it('routes explicit chart formats to their renderers', () => {
    const { rerender } = render(<ChartBlock option={{}} spec_format="plotly" />);
    expect(screen.getByText('Plotly renderer')).toBeInTheDocument();
    rerender(<ChartBlock option={{}} spec_format="vegalite" />);
    expect(screen.getByText('Vega renderer')).toBeInTheDocument();
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
