import {
  init,
  registerTheme,
  use as register,
} from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import {
  BarChart,
  BoxplotChart,
  CandlestickChart,
  FunnelChart,
  GaugeChart,
  HeatmapChart,
  LineChart,
  PieChart,
  RadarChart,
  SankeyChart,
  ScatterChart,
  SunburstChart,
  TreemapChart,
} from 'echarts/charts';
import {
  DataZoomComponent,
  DatasetComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  MarkPointComponent,
  TitleComponent,
  ToolboxComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components';
import { registerAllThemes } from '../../../constants/echartsThemes';

register([
  CanvasRenderer,
  LineChart,
  BarChart,
  PieChart,
  ScatterChart,
  RadarChart,
  HeatmapChart,
  FunnelChart,
  BoxplotChart,
  TreemapChart,
  SunburstChart,
  SankeyChart,
  GaugeChart,
  CandlestickChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  ToolboxComponent,
  DataZoomComponent,
  TitleComponent,
  VisualMapComponent,
  MarkLineComponent,
  MarkPointComponent,
  DatasetComponent,
]);
registerAllThemes(registerTheme);

export { init };
