import { describe, expect, it } from 'vitest';
import type { Message } from '../../../../types/message';
import { taskStepPresentation } from '../taskStepPresentation';

const step = (tool_name: string, status = 'completed') => ({
  type: 'tool_step', tool_name, status, tool_call_id: tool_name,
});
const content = (items: unknown[]) => items as Message['content'];
const marker = { type: 'task_progress', root_task_id: '00000000-0000-4000-8000-000000000001', presentation: 'model_directed' };

describe('Model-directed card presentation', () => {
  it('keeps persisted legacy transcripts visible', () => {
    expect(taskStepPresentation(content([step('start_task_execution'), step('complete_task_step')])).hidden.size).toBe(0);
  });
  it('removes only internal management cards, keeping business errors and results', () => {
    const view = taskStepPresentation(content([marker, step('start_task_execution'), step('save_task_text'),
      step('web_search'), step('code_execute', 'error'), step('knowledge_search'), step('erp_agent')]));
    expect([...view.hidden]).toEqual([1, 2]);
    expect(view.hidden.has(4)).toBe(false);
  });
  it('coalesces recovery cards without changing the stored transcript', () => {
    const transcript = content([marker, step('find_task_text'), step('read_task_text'), step('reuse_task_image')]);
    const original = JSON.stringify(transcript);
    const view = taskStepPresentation(transcript);
    expect([...view.hidden]).toEqual([1, 2]);
    expect(view.recovery.has(3)).toBe(true);
    expect(JSON.stringify(transcript)).toBe(original);
  });
});

it('retains failed recovery and internal failures instead of coalescing them into success', () => {
  const transcript = content([marker, step('read_task_text', 'error'), step('read_task_text'),
    step('complete_task_step', 'error'), step('reuse_task_image')]);
  const view = taskStepPresentation(transcript);
  expect(view.hidden.has(1)).toBe(false);
  expect(view.hidden.has(3)).toBe(false);
  expect(view.hidden.has(2)).toBe(true);
});
