import type { Message } from '../../../types/message';

const internal = new Set([
  'resume_task_execution',
  'start_task_execution', 'propose_task_revision', 'update_task_plan',
  'save_task_text', 'complete_task_step', 'review_task_plan',
  'bind_task_image_sources', 'import_task_result', 'import_task_receipt',
  'import_task_code_result', 'read_task_result', 'read_task_write_result',
  'read_task_scheduled_result', 'read_task_code_result',
]);
const reuse = new Set([
  'find_task_text', 'read_task_text', 'reuse_task_text',
  'reuse_task_image', 'import_task_source',
]);

export function taskStepPresentation(content: Message['content']) {
  // Legacy transcripts keep their original presentation. New delivery marks
  // the task protocol explicitly; tool names alone never erase old history.
  const modern = content.some(part => (part.type === 'task_progress' || part.type === 'tool_step')
    && part.presentation === 'model_directed');
  const hidden = new Set<number>();
  const recovery = new Set<number>();
  if (!modern) return { hidden, recovery };
  let latestReuse: number | undefined;
  content.forEach((part, index) => {
    if (part.type !== 'tool_step') return;
    // Keep actionable failures visible until a real final task status is
    // available. Coalescing must not turn an unsuccessful reuse into success.
    if (part.status === 'error' || part.status === 'cancelled') return;
    if (internal.has(part.tool_name)) hidden.add(index);
    if (reuse.has(part.tool_name)) {
      if (latestReuse !== undefined) hidden.add(latestReuse);
      latestReuse = index;
      recovery.add(index);
    }
  });
  return { hidden, recovery };
}
