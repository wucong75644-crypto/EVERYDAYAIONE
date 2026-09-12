/**
 * TaskRunHistory — 任务执行历史列表
 *
 * 显示最近 N 次执行：状态、耗时、积分、错误信息
 */
import { lazy, Suspense, useEffect } from 'react';
import { TableBlock } from '../chat/message/TableBlock';
import ChartBlock from '../chat/message/ChartBlock';
import FileCardList from '../chat/media/FileCard';
import type { ContentPart } from '../../types/message';
const DiagramBlock = lazy(() => import('../chat/message/DiagramBlock'));
import { CheckCircle2, XCircle, Clock, Loader2, FileText } from 'lucide-react';
import { useScheduledTaskStore } from '../../stores/useScheduledTaskStore';
import { cn } from '../../utils/cn';
import type { TaskRun } from '../../types/scheduledTask';

interface Props {
  taskId: string;
}

const STATUS_CONFIG = {
  success: { icon: CheckCircle2, color: 'text-success', label: '成功' },
  failed:  { icon: XCircle,      color: 'text-error',   label: '失败' },
  timeout: { icon: Clock,        color: 'text-warning', label: '超时' },
  running: { icon: Loader2,      color: 'text-accent',  label: '执行中' },
  skipped: { icon: Clock,        color: 'text-text-tertiary', label: '跳过' },
} as const;

function formatRelativeTime(iso: string): string {
  try {
    const date = new Date(iso);
    const diff = Date.now() - date.getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return '刚刚';
    if (mins < 60) return `${mins} 分钟前`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours} 小时前`;
    const days = Math.floor(hours / 24);
    if (days < 7) return `${days} 天前`;
    return date.toLocaleDateString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

function formatDuration(ms?: number | null): string {
  if (!ms) return '';
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function ResultBlock({ block, runId }: { block: ContentPart; runId: string }) {
  if (block.type === 'table') return <TableBlock {...block} />;
  if (block.type === 'chart') return <ChartBlock {...block} />;
  if (block.type === 'file') return <FileCardList files={[block]} />;
  if (block.type === 'diagram') return <Suspense fallback={<p>关系图加载中…</p>}><DiagramBlock diagram={block} messageId={`scheduled-run-${runId}`} /></Suspense>;
  if (block.type === 'image' && block.url) return <a href={block.url} target="_blank" rel="noopener noreferrer"><img src={block.url} alt={block.alt || '执行结果'} className="max-w-full" /></a>;
  if (block.type === 'text') return <p className="whitespace-pre-wrap">{block.text}</p>;
  return null;
}

function RunRow({ run }: { run: TaskRun }) {
  const config = STATUS_CONFIG[run.status] || STATUS_CONFIG.skipped;
  const Icon = config.icon;
  const isRunning = run.status === 'running';

  return (
    <div className="flex items-start gap-2 py-2 px-3 hover:bg-[var(--s-hover)] rounded-md transition-colors">
      <Icon
        className={cn(
          'w-3.5 h-3.5 mt-0.5 shrink-0',
          config.color,
          isRunning && 'animate-spin',
        )}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-xs">
          <span className="text-[var(--s-text-secondary)]">
            {formatRelativeTime(run.started_at)}
          </span>
          <span className={cn('font-medium', config.color)}>{config.label}</span>
          {run.duration_ms != null && (
            <span className="text-[var(--s-text-tertiary)]">{formatDuration(run.duration_ms)}</span>
          )}
          {run.credits_used > 0 && (
            <span className="text-[var(--s-text-tertiary)]">{run.credits_used} 积分</span>
          )}
        </div>
        {run.result_summary && (
          <p className="text-xs text-[var(--s-text-secondary)] mt-0.5 whitespace-pre-wrap">
            {run.result_summary}
          </p>
        )}
        {run.error_message && (
          <p className="text-xs text-[var(--s-error)] mt-0.5 line-clamp-2">
            {run.error_message}
          </p>
        )}
        {run.events && run.events.length > 0 && (
          <div className="mt-1 space-y-0.5 border-l border-[var(--s-border-subtle)] pl-2">
            {run.events.map((event, index) => (
              <p key={`${event.event_type}-${event.tool_name || index}`} className="text-[11px] text-[var(--s-text-tertiary)]">
                {event.tool_name || event.event_type} · {event.status}
                {event.summary ? ` · ${event.summary}` : ''}
              </p>
            ))}
          </div>
        )}
        {run.content_blocks?.map((block, index) => <ResultBlock key={index} block={block} runId={run.id} />)}
        {!run.content_blocks?.length && run.result_files && run.result_files.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1">
            {run.result_files.filter((file) => file.url).map((f, i) => (
              <a
                key={i}
                href={f.url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-xs text-[var(--s-accent)] hover:underline"
              >
                <FileText className="w-3 h-3" />
                {f.name}
              </a>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export function TaskRunHistory({ taskId }: Props) {
  const runs = useScheduledTaskStore((s) => s.runs[taskId]);
  const fetchRuns = useScheduledTaskStore((s) => s.fetchRuns);
  const fetchTasks = useScheduledTaskStore((s) => s.fetchTasks);
  const running = useScheduledTaskStore((s) => s.tasks.some((task) => task.id === taskId && task.status === 'running'));
  const hasRunningRun = runs?.some((run) => run.status === 'running') ?? false;

  useEffect(() => {
    fetchRuns(taskId);
  }, [taskId, fetchRuns]);

  useEffect(() => {
    if (!running && !hasRunningRun) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      await Promise.all([fetchRuns(taskId), fetchTasks({ quiet: true })]);
      if (!disposed) timer = setTimeout(refresh, 5000);
    };
    timer = setTimeout(refresh, 5000);
    return () => { disposed = true; clearTimeout(timer); };
  }, [taskId, running, hasRunningRun, fetchRuns, fetchTasks]);

  if (!runs) {
    return (
      <div className="text-xs text-[var(--s-text-tertiary)] py-2 px-3">
        加载执行历史...
      </div>
    );
  }

  if (runs.length === 0) {
    return (
      <div className="text-xs text-[var(--s-text-tertiary)] py-2 px-3">
        暂无执行记录
      </div>
    );
  }

  return (
    <div className="border-t border-[var(--s-border-subtle)] mt-3 pt-2">
      <div className="flex items-center gap-2 px-3 mb-1">
        <span className="text-xs font-medium text-[var(--s-text-tertiary)] uppercase tracking-wider">执行结果与历史</span>
      </div>
      <div className="max-h-[60vh] overflow-y-auto">
        {runs.slice(0, 10).map((run) => (
          <RunRow key={run.id} run={run} />
        ))}
      </div>
    </div>
  );
}
