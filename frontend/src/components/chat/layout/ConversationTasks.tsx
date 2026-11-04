import { useMemo, useState } from 'react';
import { ListChecks } from 'lucide-react';
import { Popover } from '../../primitives/Popover';
import TaskProgressCard from '../message/TaskProgressCard';
import { getTextContent, normalizeMessage, type Message } from '../../../stores/useMessageStore';

function conversationTasks(messages: readonly Message[]) {
  const tasks = new Map<string, { rootTaskId: string; messageId: string; title: string; presentation?: 'model_directed'; planRevision?: string }>();
  let title = '';
  for (const source of messages) {
    const message = Array.isArray(source.content) ? source : normalizeMessage(source);
    if (message.role === 'user') title = getTextContent(message).trim().slice(0, 100);
    if (message.role !== 'assistant') continue;
    for (const part of message.content) {
      if (part.type !== 'task_progress') continue;
      // A plan can outlive the chat turn that recorded it.
      const planId = part.plan_id ?? part.root_task_id;
      const previous = tasks.get(planId);
      tasks.delete(planId);
      tasks.set(planId, { rootTaskId: part.root_task_id, messageId: message.id,
        title: previous?.title || title || '工作计划', presentation: part.presentation, planRevision: part.plan_revision });
    }
  }
  return [...tasks.values()].reverse();
}

export default function ConversationTasks({ messages, onResume }: {
  messages: readonly Message[];
  onResume: (messageId: string) => Promise<boolean>;
}) {
  const tasks = useMemo(() => conversationTasks(messages), [messages]);
  const [visible, setVisible] = useState<Record<string, boolean | null>>({});
  const keyOf = (task: typeof tasks[number]) => `${task.rootTaskId}:${task.messageId}:${task.planRevision ?? ''}`;
  const count = tasks.filter(task => visible[keyOf(task)]).length;
  const resolved = tasks.every(task => visible[keyOf(task)] != null);
  return <Popover align="end" maxWidth={400} className="w-[min(400px,calc(100vw-16px))]"
    anchor={<span aria-hidden="true" className="fixed right-2 top-14 h-0 w-0 sm:right-4" />}
    trigger={<button type="button" aria-label="查看任务" title="查看当前会话任务"
      className="flex items-center gap-1 rounded-lg p-1.5 text-text-tertiary hover:bg-hover transition-base">
      <ListChecks className="h-5 w-5" />
      {count > 0 && <span className="text-xs tabular-nums">{count}</span>}
    </button>}>
    <h2 className="mb-3 text-sm font-medium text-text-primary">任务计划</h2>
    <div className="max-h-[min(65vh,560px)] overflow-y-auto overscroll-contain">
      {resolved && count === 0 && <p className="py-4 text-sm text-text-tertiary">暂无工作计划</p>}
      {tasks.map(task => <div key={keyOf(task)} className="mb-3 last:mb-0">
        {visible[keyOf(task)] && <p className="truncate text-sm text-text-primary" title={task.title}>{task.title}</p>}
        <TaskProgressCard rootTaskId={task.rootTaskId} messageId={task.messageId}
          presentation={task.presentation} requirePlan
          onVisibility={shown => setVisible(previous => previous[keyOf(task)] === shown ? previous : { ...previous, [keyOf(task)]: shown })}
          onResume={() => onResume(task.messageId)} />
      </div>)}
    </div>
  </Popover>;
}
