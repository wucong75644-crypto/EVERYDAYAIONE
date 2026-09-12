import { beforeEach, describe, expect, it, vi } from 'vitest';
import api from '../api';
import { scheduledTaskService } from '../scheduledTask';
vi.mock('../api', () => ({ default: { post: vi.fn() } }));
describe('scheduled task structured parser transport', () => {
  beforeEach(() => vi.mocked(api.post).mockResolvedValue({ data: { data: { prompt: '学习建议' } } }));
  it.each(['create', 'update'] as const)('uses structured fields for panel %s', async (operation) => {
    expect(await scheduledTaskService.parseNL('学习建议', operation)).toEqual({ prompt: '学习建议' });
    expect(api.post).toHaveBeenLastCalledWith('/scheduled-tasks/parse', {
      text: '学习建议', operation, explicit_fields_only: true, structured_fields: true,
    });
  });
  it('keeps the old invocation contract', async () => {
    await scheduledTaskService.parseNL('学习建议');
    expect(api.post).toHaveBeenLastCalledWith('/scheduled-tasks/parse', { text: '学习建议' });
  });
});
