import { it, expect, vi } from 'vitest';
import { applyTaskResume } from '../taskResume';
const ports = () => ({ beginStreaming: vi.fn(), subscribe: vi.fn(), reload: vi.fn().mockResolvedValue(undefined) });
it.each(['enqueued', 'already_enqueued', 'already_pending'])('keeps the old source for logical %s', async outcome => {
  const delivery = ports();
  expect(await applyTaskResume({ success: true, resumed_count: 1, tasks: [{ outcome, logical_task_id: 'root',
    client_task_id: 'old-client', assistant_message_id: 'source', conversation_id: 'conv' }] }, 'conv', 'source', delivery)).toBe(true);
  expect(delivery.beginStreaming).not.toHaveBeenCalled(); expect(delivery.subscribe).not.toHaveBeenCalled();
  expect(delivery.reload).toHaveBeenCalledTimes(1);
});
it('subscribes only the real new continuation message', async () => {
  const delivery = ports();
  await applyTaskResume({ success: true, resumed_count: 1, tasks: [{ outcome: 'enqueued', client_task_id: 'new-client',
    assistant_message_id: 'new-message', conversation_id: 'conv' }] }, 'conv', 'source', delivery);
  expect(delivery.subscribe).toHaveBeenCalledExactlyOnceWith('new-client', 'conv');
  expect(delivery.beginStreaming).not.toHaveBeenCalled();
});
it('keeps the original paused-window streaming path', async () => {
  const delivery = ports();
  await applyTaskResume({ success: true, resumed_count: 1, tasks: [{ outcome: 'resumed', client_task_id: 'original',
    assistant_message_id: 'source' }] }, 'conv', 'source', delivery);
  expect(delivery.beginStreaming).toHaveBeenCalledExactlyOnceWith('conv', 'source');
  expect(delivery.reload).not.toHaveBeenCalled();
});
it('does not suggest resending an accepted resume when history refresh fails', async () => {
  const delivery = ports(); delivery.reload.mockRejectedValue(new Error('offline'));
  expect(await applyTaskResume({ success: true, resumed_count: 1, tasks: [{ outcome: 'enqueued' }] }, 'conv', 'source', delivery)).toBe(true);
  expect(delivery.beginStreaming).not.toHaveBeenCalled();
});
it.each(['busy', 'terminal', 'checkpoint_missing', 'not_resumable'])('rejects %s', async outcome => {
  const delivery = ports();
  expect(await applyTaskResume({ success: true, resumed_count: 0, tasks: [{ outcome }] }, 'conv', 'source', delivery)).toBe(false);
  expect(delivery.beginStreaming).not.toHaveBeenCalled(); expect(delivery.reload).not.toHaveBeenCalled();
});
it('rejects another conversation', async () => {
  const delivery = ports();
  expect(await applyTaskResume({ success: true, resumed_count: 1, tasks: [{ outcome: 'resumed', client_task_id: 'other',
    conversation_id: 'other-conv' }] }, 'conv', 'source', delivery)).toBe(false);
  expect(delivery.subscribe).not.toHaveBeenCalled();
});
it('uses the native continuation ID when an internal task has no external IDs', async () => {
  const delivery = ports();
  expect(await applyTaskResume({ success: true, resumed_count: 1, tasks: [{ outcome: 'enqueued', task_id: 'native-task',
    assistant_message_id: 'new-message', conversation_id: 'conv' }] }, 'conv', 'source', delivery)).toBe(true);
  expect(delivery.subscribe).toHaveBeenCalledExactlyOnceWith('native-task', 'conv');
  expect(delivery.beginStreaming).not.toHaveBeenCalled();
});
