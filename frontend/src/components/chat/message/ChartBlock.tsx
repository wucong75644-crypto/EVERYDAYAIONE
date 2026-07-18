import { lazy, memo, Suspense } from 'react';

interface ChartBlockProps {
  option: Record<string, unknown>;
  title?: string;
  spec_format?: 'echarts' | 'plotly' | 'vegalite' | 'unknown';
  messageId?: string;
}

const EChartsRenderer = lazy(() => import('./EChartsRenderer'));
const PlotlyBlock = lazy(() => import('./PlotlyBlock'));
const VegaLiteBlock = lazy(() => import('./VegaLiteBlock'));

function RendererLoading({ label }: { label: string }) {
  return (
    <div className="my-3 flex min-h-32 items-center justify-center rounded-xl bg-hover text-sm text-text-disabled">
      {label}加载中...
    </div>
  );
}

function UnknownChartFallback({
  option,
  specFormat,
}: {
  option: Record<string, unknown>;
  specFormat: string;
}) {
  return (
    <div className="my-3 rounded-xl border border-border-default bg-surface-card p-4">
      <div className="text-sm font-medium text-error">不支持的图表格式：{specFormat}</div>
      <pre className="mt-2 max-h-80 overflow-auto rounded bg-hover p-2 text-xs text-text-secondary">
        {JSON.stringify(option, null, 2)}
      </pre>
    </div>
  );
}

function ChartBlockInner({
  option,
  title,
  spec_format = 'echarts',
  messageId,
}: ChartBlockProps) {
  if (spec_format === 'plotly') {
    return (
      <Suspense fallback={<RendererLoading label="历史 Plotly 图表" />}>
        <PlotlyBlock option={option} title={title} />
      </Suspense>
    );
  }
  if (spec_format === 'vegalite') {
    return (
      <Suspense fallback={<RendererLoading label="历史 Vega-Lite 图表" />}>
        <VegaLiteBlock option={option} title={title} />
      </Suspense>
    );
  }
  if (spec_format !== 'echarts') {
    return <UnknownChartFallback option={option} specFormat={spec_format} />;
  }
  return (
    <Suspense fallback={<RendererLoading label="数据图表" />}>
      <EChartsRenderer option={option} title={title} messageId={messageId} />
    </Suspense>
  );
}

export default memo(ChartBlockInner);
