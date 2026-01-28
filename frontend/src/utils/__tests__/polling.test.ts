/**
 * polling 工具单元测试
 * 测试轮询管理器功能
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { PollingManager } from '../polling';

describe('PollingManager', () => {
  let pollingManager: PollingManager;

  beforeEach(() => {
    pollingManager = new PollingManager();
    vi.useFakeTimers();
  });

  afterEach(() => {
    pollingManager.stopAll();
    vi.restoreAllMocks();
  });

  it('应该成功启动轮询', () => {
    const taskId = 'task-1';
    const pollFn = vi.fn().mockResolvedValue({ done: false });
    const onSuccess = vi.fn();
    const onError = vi.fn();

    pollingManager.start(taskId, pollFn, { onSuccess, onError });

    expect(pollingManager.has(taskId)).toBe(true);
    expect(pollingManager.size).toBe(1);
  });

  it('应该在任务完成时调用 onSuccess', async () => {
    const taskId = 'task-1';
    const result = { data: 'success' };
    const pollFn = vi.fn()
      .mockResolvedValueOnce({ done: false })
      .mockResolvedValueOnce({ done: true, result });

    const onSuccess = vi.fn();
    const onError = vi.fn();

    pollingManager.start(taskId, pollFn, { onSuccess, onError }, { interval: 100 });

    // 立即执行一次
    await vi.runOnlyPendingTimersAsync();

    // 等待第二次轮询
    await vi.advanceTimersByTimeAsync(100);

    expect(onSuccess).toHaveBeenCalledWith(result);
    expect(onError).not.toHaveBeenCalled();
    expect(pollingManager.has(taskId)).toBe(false);
  });

  it('应该在任务失败时调用 onError', async () => {
    const taskId = 'task-1';
    const error = new Error('Task failed');
    const pollFn = vi.fn()
      .mockResolvedValueOnce({ done: false })
      .mockResolvedValueOnce({ done: true, error });

    const onSuccess = vi.fn();
    const onError = vi.fn();

    pollingManager.start(taskId, pollFn, { onSuccess, onError }, { interval: 100 });

    await vi.runOnlyPendingTimersAsync();
    await vi.advanceTimersByTimeAsync(100);

    expect(onError).toHaveBeenCalledWith(error);
    expect(onSuccess).not.toHaveBeenCalled();
    expect(pollingManager.has(taskId)).toBe(false);
  });

  it('应该在超时后停止轮询', async () => {
    const taskId = 'task-1';
    const pollFn = vi.fn().mockResolvedValue({ done: false });
    const onSuccess = vi.fn();
    const onError = vi.fn();

    pollingManager.start(
      taskId,
      pollFn,
      { onSuccess, onError },
      { interval: 100, maxDuration: 500 }
    );

    // 模拟超过最大时长
    await vi.advanceTimersByTimeAsync(600);

    expect(onError).toHaveBeenCalledWith(
      expect.objectContaining({
        message: expect.stringContaining('轮询超时'),
      })
    );
    expect(pollingManager.has(taskId)).toBe(false);
  });

  it('应该处理轮询函数抛出的异常', async () => {
    const taskId = 'task-1';
    const pollFn = vi.fn().mockRejectedValue(new Error('Network error'));
    const onSuccess = vi.fn();
    const onError = vi.fn();

    // Mock console.warn to avoid test output noise
    const consoleWarnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

    pollingManager.start(taskId, pollFn, { onSuccess, onError }, { interval: 100 });

    await vi.runOnlyPendingTimersAsync();

    // 异常不应该停止轮询
    expect(pollingManager.has(taskId)).toBe(true);
    expect(onError).not.toHaveBeenCalled();
    expect(consoleWarnSpy).toHaveBeenCalled();

    consoleWarnSpy.mockRestore();
  });

  it('应该能够手动停止轮询', () => {
    const taskId = 'task-1';
    const pollFn = vi.fn().mockResolvedValue({ done: false });
    const onSuccess = vi.fn();
    const onError = vi.fn();

    pollingManager.start(taskId, pollFn, { onSuccess, onError });
    expect(pollingManager.has(taskId)).toBe(true);

    pollingManager.stop(taskId);
    expect(pollingManager.has(taskId)).toBe(false);
  });

  it('应该能够停止所有轮询', () => {
    const pollFn = vi.fn().mockResolvedValue({ done: false });
    const callbacks = { onSuccess: vi.fn(), onError: vi.fn() };

    pollingManager.start('task-1', pollFn, callbacks);
    pollingManager.start('task-2', pollFn, callbacks);
    pollingManager.start('task-3', pollFn, callbacks);

    expect(pollingManager.size).toBe(3);

    pollingManager.stopAll();
    expect(pollingManager.size).toBe(0);
  });

  it('应该防止竞态条件（原子性检查）', async () => {
    const taskId = 'task-1';
    let callCount = 0;
    const pollFn = vi.fn().mockImplementation(async () => {
      callCount++;
      if (callCount === 2) {
        // 第二次调用返回完成
        return { done: true, result: 'success' };
      }
      return { done: false };
    });

    const onSuccess = vi.fn();
    const onError = vi.fn();

    pollingManager.start(taskId, pollFn, { onSuccess, onError }, { interval: 50 });

    // 第一次调用（立即）
    await vi.runOnlyPendingTimersAsync();

    // 第二次调用（50ms后）
    await vi.advanceTimersByTimeAsync(50);

    // onSuccess 应该只被调用一次
    expect(onSuccess).toHaveBeenCalledTimes(1);
    expect(pollingManager.has(taskId)).toBe(false);
  });

  it('应该返回清理函数', () => {
    const taskId = 'task-1';
    const pollFn = vi.fn().mockResolvedValue({ done: false });
    const callbacks = { onSuccess: vi.fn(), onError: vi.fn() };

    const cleanup = pollingManager.start(taskId, pollFn, callbacks);

    expect(pollingManager.has(taskId)).toBe(true);
    cleanup();
    expect(pollingManager.has(taskId)).toBe(false);
  });
});
