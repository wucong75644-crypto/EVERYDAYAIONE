/**
 * taskRestoration 多图恢复单元测试
 *
 * 测试 restoreMediaTask 恢复多图占位符时：
 * - generation_params 包含 num_images
 * - 无 num_images 的旧任务正常恢复（向后兼容）
 * - 超时任务不恢复
 * - 无 conversation_id 的任务不恢复
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { restoreMediaTask, type PendingTask } from '../taskRestoration';
import { IMAGE_TASK_TIMEOUT } from '../../config/task';

// ============================================================
// Mock 依赖
// ============================================================

const mockAddMessage = vi.fn();
const mockMarkForceRefresh = vi.fn();

vi.mock('../../stores/useMessageStore', () => ({
  useMessageStore: {
    getState: () => ({
      addMessage: mockAddMessage,
      markForceRefresh: mockMarkForceRefresh,
    }),
  },
}));

vi.mock('../../services/api', () => ({
  default: { get: vi.fn() },
}));

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('../logger', () => ({
  logger: {
    info: vi.fn(),
    debug: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  },
}));

// ============================================================
// 辅助函数
// ============================================================

function createPendingImageTask(overrides: Partial<PendingTask> = {}): PendingTask {
  return {
    id: 'task-1',
    external_task_id: 'ext-task-1',
    conversation_id: 'conv-1',
    type: 'image',
    status: 'running',
    request_params: {
      model: 'nano-banana',
      aspect_ratio: '1:1',
    },
    credits_locked: 5,
    placeholder_message_id: 'msg-1',
    placeholder_created_at: new Date().toISOString(),
    started_at: new Date().toISOString(),
    last_polled_at: null,
    client_task_id: 'client-task-1',
    ...overrides,
  };
}

// ============================================================
// 测试
// ============================================================

describe('restoreMediaTask', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should include num_images in generation_params when present', () => {
    const task = createPendingImageTask({
      request_params: {
        model: 'nano-banana',
        aspect_ratio: '1:1',
        num_images: '4',
      },
    });

    restoreMediaTask(task);

    expect(mockAddMessage).toHaveBeenCalledOnce();
    const addedMessage = mockAddMessage.mock.calls[0][1];
    expect(addedMessage.generation_params.num_images).toBe('4');
    expect(addedMessage.generation_params.type).toBe('image');
    expect(addedMessage.generation_params.model).toBe('nano-banana');
  });

  it('should not include num_images when absent (backward compatibility)', () => {
    const task = createPendingImageTask({
      request_params: {
        model: 'nano-banana',
        aspect_ratio: '1:1',
        // no num_images
      },
    });

    restoreMediaTask(task);

    expect(mockAddMessage).toHaveBeenCalledOnce();
    const addedMessage = mockAddMessage.mock.calls[0][1];
    expect(addedMessage.generation_params).toEqual({
      type: 'image',
      model: 'nano-banana',
    });
    expect(addedMessage.generation_params).not.toHaveProperty('num_images');
  });

  it('should include num_images=1 for single image task', () => {
    const task = createPendingImageTask({
      request_params: {
        model: 'nano-banana',
        num_images: '1',
      },
    });

    restoreMediaTask(task);

    const addedMessage = mockAddMessage.mock.calls[0][1];
    expect(addedMessage.generation_params.num_images).toBe('1');
  });

  it('should not restore timed-out tasks', () => {
    const task = createPendingImageTask({
      started_at: new Date(Date.now() - IMAGE_TASK_TIMEOUT - 1000).toISOString(),
    });

    restoreMediaTask(task);

    expect(mockAddMessage).not.toHaveBeenCalled();
    expect(mockMarkForceRefresh).not.toHaveBeenCalled();
  });

  it('should not restore tasks without conversation_id', () => {
    const task = createPendingImageTask({
      conversation_id: '',
    });

    restoreMediaTask(task);

    expect(mockAddMessage).not.toHaveBeenCalled();
  });

  it('should mark force refresh for valid tasks', () => {
    const task = createPendingImageTask();

    restoreMediaTask(task);

    expect(mockMarkForceRefresh).toHaveBeenCalledWith('conv-1');
  });

  it('should use placeholder_message_id as message id', () => {
    const task = createPendingImageTask({
      placeholder_message_id: 'placeholder-msg-42',
    });

    restoreMediaTask(task);

    const addedMessage = mockAddMessage.mock.calls[0][1];
    expect(addedMessage.id).toBe('placeholder-msg-42');
  });

  it('should set pending status and loading text for image', () => {
    const task = createPendingImageTask();

    restoreMediaTask(task);

    const addedMessage = mockAddMessage.mock.calls[0][1];
    expect(addedMessage.status).toBe('pending');
    expect(addedMessage.content[0].text).toBe('图片生成中');
  });

  it('should restore video task with correct loading text', () => {
    const task = createPendingImageTask({
      type: 'video',
    });

    restoreMediaTask(task);

    const addedMessage = mockAddMessage.mock.calls[0][1];
    expect(addedMessage.content[0].text).toBe('视频生成中');
  });
});
