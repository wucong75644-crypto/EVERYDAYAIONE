import { useEffect, useRef, useState } from 'react';
import { isAxiosError } from 'axios';
import { pauseTaskByMessageId, cancelTaskByMessageId } from '../../../services/message';
import { useInView } from 'react-intersection-observer';
import { getTaskProgress, type TaskProgress } from '../../../services/taskExecution';

const labels: Record<TaskProgress['status'], string> = {
  active: '正在推进', waiting_input: '等待补充信息', waiting_approval: '等待确认',
  waiting_dependencies: '等待任务结果', paused: '已暂停', completed: '全部完成',
  partial: '尚有未完成事项', failed: '执行未完成', cancelled: '已取消',
};
const stepLabels: Record<TaskProgress['steps'][number]['status'], string> = {
  needs_verification: '结果待核实', waiting_result: '已受理，等待结果', completed: '已完成',
  paused: '已暂停', cancelled: '已取消', needs_attention: '需要处理', ready: '待执行',
  waiting_dependency: '等待前置步骤',
};
const pausable = new Set(['active', 'waiting_input', 'waiting_approval', 'waiting_dependencies']);

type Props = { rootTaskId: string; messageId: string; onResume?: () => Promise<boolean> };
export default function TaskProgressCard(props: Props) {
  return <ProgressView key={`${props.rootTaskId}:${props.messageId}`} {...props} />;
}

function ProgressView({ rootTaskId, messageId, onResume }: Props) {
  const { ref, inView } = useInView({ rootMargin: '100px' });
  const [value, setValue] = useState<TaskProgress | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const controlling = useRef(false);
  const latest = useRef<TaskProgress | null>(null);
  const control = async (action: 'pause' | 'resume' | 'cancel') => {
    if (controlling.current) return;
    controlling.current = true; setBusy(true); setNotice('');
    try {
      if (action === 'pause') {
        await pauseTaskByMessageId(messageId);
        setNotice('已提交暂停请求，等待安全保存。');
      } else if (action === 'cancel') {
        await cancelTaskByMessageId(messageId);
        setNotice('已提交停止请求。已有成果保留，已派发操作仍需核实。');
      } else if (onResume && await onResume()) {
        setNotice('继续请求已受理，保留原成果等待接续。');
      }
    } catch { setNotice('请求结果未确认，请先刷新进度，勿重复提交。'); }
    finally { controlling.current = false; setBusy(false); setRefresh(n => n + 1); }
  };

  useEffect(() => {
    if (!inView) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    const load = async () => {
      if (stopped || document.hidden) return;
      controller?.abort();
      const request = new AbortController();
      controller = request;
      let delay = 30_000;
      try {
        const next = await getTaskProgress(rootTaskId, messageId, request.signal);
        if (stopped || request.signal.aborted) return;
        if (!latest.current || next.revision >= latest.current.revision) latest.current = next;
        const current = latest.current;
        setValue(current);
        setUnavailable(false);
        // Closed windows can acquire a new authenticated follow-up owner.
        delay = current.status === 'active' ? 5_000 : pausable.has(current.status) || current.status === 'paused' ? 30_000 : 60_000;
      } catch (error) {
        if (stopped || request.signal.aborted) return;
        // A stale snapshot is never presented as current success after revocation.
        latest.current = null;
        setValue(null);
        setUnavailable(true);
        if (isAxiosError(error) && [401, 403, 404].includes(error.response?.status ?? 0)) return;
      }
      if (!stopped) timer = setTimeout(() => { void load(); }, delay);
    };
    const visible = () => {
      clearTimeout(timer);
      controller?.abort();
      if (!document.hidden) void load();
    };
    document.addEventListener('visibilitychange', visible);
    void load();
    return () => {
      stopped = true;
      clearTimeout(timer);
      controller?.abort();
      document.removeEventListener('visibilitychange', visible);
    };
  }, [rootTaskId, messageId, inView, refresh]);

  return <section ref={ref} aria-label="任务进度" className="my-2 rounded-xl border border-border-default px-3 py-2 text-sm text-text-secondary">
    <div className="flex items-center justify-between gap-3">
      <span role="status">{unavailable ? '暂时无法读取任务进度' : value ? labels[value.status] : '正在读取任务进度'}</span>
      <button type="button" className="text-xs text-text-tertiary hover:text-text-primary" onClick={() => setRefresh(n => n + 1)}>刷新进度</button>
    </div>
    {notice && <p aria-live="polite" className="mt-1 text-xs">{notice}</p>}
    {value && <>
      <p className="mt-1 text-xs text-text-tertiary">已完成 {value.completed_steps}/{value.total_steps} 个步骤 · 已满足 {value.completed_requirements}/{value.total_requirements} 项交付要求</p>
      {value.pending_results > 0 && <p className="mt-1 text-xs">有 {value.pending_results} 项操作已受理，尚未确认最终结果。</p>}
      {value.unconfirmed_results > 0 && <p className="mt-1 text-xs">有 {value.unconfirmed_results} 项操作结果待核实，请勿重复提交。</p>}
      {(pausable.has(value.status) || value.status === 'paused') && <div className="mt-2 flex gap-3">
        {pausable.has(value.status) && <button type="button" disabled={busy} className="text-xs text-text-tertiary hover:text-text-primary disabled:opacity-50" onClick={() => { void control('pause'); }}>暂停任务</button>}
        {value.status === 'paused' && onResume && <button type="button" disabled={busy} className="text-xs text-text-tertiary hover:text-text-primary disabled:opacity-50" onClick={() => { void control('resume'); }}>继续任务</button>}
        <button type="button" disabled={busy} className="text-xs text-text-tertiary hover:text-text-primary disabled:opacity-50" onClick={() => { void control('cancel'); }}>停止任务</button>
      </div>}
      {value.steps.length > 0 && <details className="mt-2">
        <summary className="cursor-pointer text-xs">查看步骤</summary>
        <ol className="mt-1 space-y-1">
          {value.steps.map(step => <li key={step.id} className="flex justify-between gap-3 text-xs">
            <span className="min-w-0 break-words">{step.title}</span><span className="shrink-0 text-text-tertiary">{stepLabels[step.status]}</span>
          </li>)}
        </ol>
      </details>}
    </>}
  </section>;
}
