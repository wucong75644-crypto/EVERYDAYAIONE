/**
 * useScheduledTaskStore 单元测试
 *
 * 覆盖：
 * - fetchTasks（成功 / 失败）
 * - createTask / updateTask / deleteTask
 * - pauseTask / resumeTask / runTaskNow
 * - 视图模式切换
 * - 乐观更新（add / remove / update）
 * - 执行历史拉取
 * - clear
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useScheduledTaskStore } from '../useScheduledTaskStore';
import type { ScheduledTask, TaskRun } from '../../types/scheduledTask';

// Mock scheduledTaskService
vi.mock('../../services/scheduledTask', () => ({
  scheduledTaskService: {
    list: vi.fn(),
    create: vi.fn(),
    get: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    pause: vi.fn(),
    resume: vi.fn(),
    runNow: vi.fn(),
    listRuns: vi.fn(),
    listChatTargets: vi.fn(),
    parseNL: vi.fn(),
  },
}));

vi.mock('../../utils/logger', () => ({
  logger: {
    info: vi.fn(),
    debug: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  },
}));

import { scheduledTaskService } from '../../services/scheduledTask';

const mockList = vi.mocked(scheduledTaskService.list);
const mockCreate = vi.mocked(scheduledTaskService.create);
const mockGet = vi.mocked(scheduledTaskService.get);
const mockUpdate = vi.mocked(scheduledTaskService.update);
const mockDelete = vi.mocked(scheduledTaskService.delete);
const mockPause = vi.mocked(scheduledTaskService.pause);
const mockResume = vi.mocked(scheduledTaskService.resume);
const mockRunNow = vi.mocked(scheduledTaskService.runNow);
const mockListRuns = vi.mocked(scheduledTaskService.listRuns);

function makeTask(overrides: Partial<ScheduledTask> = {}): ScheduledTask {
  return {
    id: 't1',
    org_id: 'org_1',
    user_id: 'user_zhangsan',
    name: '测试任务',
    prompt: '查询销售',
    cron_expr: '0 9 * * *',
    cron_readable: '每天 09:00',
    timezone: 'Asia/Shanghai',
    push_target: { type: 'wecom_group', chatid: 'x' },
    status: 'active',
    max_credits: 10,
    retry_count: 1,
    timeout_sec: 180,
    next_run_at: null,
    last_run_at: null,
    last_summary: null,
    run_count: 0,
    consecutive_failures: 0,
    created_at: '2026-04-11T00:00:00Z',
    updated_at: '2026-04-11T00:00:00Z',
    ...overrides,
  };
}

function resetStore() {
  useScheduledTaskStore.setState({
    tasks: [],
    loading: false,
    error: null,
    viewMode: 'default',
    viewDeptId: null,
    expandedTaskId: null,
    runs: {},
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  resetStore();
});

// ════════════════════════════════════════════════════════
// fetchTasks
// ════════════════════════════════════════════════════════

describe('fetchTasks', () => {
  it('成功时存入 tasks，loading 转 false', async () => {
    const tasks = [makeTask({ id: 't1' }), makeTask({ id: 't2' })];
    mockList.mockResolvedValueOnce(tasks);

    await useScheduledTaskStore.getState().fetchTasks();

    expect(useScheduledTaskStore.getState().tasks).toEqual(tasks);
    expect(useScheduledTaskStore.getState().loading).toBe(false);
    expect(useScheduledTaskStore.getState().error).toBeNull();
    expect(mockList).toHaveBeenCalledWith('default', undefined);
  });

  it('失败时设置 error，loading 转 false', async () => {
    mockList.mockRejectedValueOnce(new Error('Network error'));

    await useScheduledTaskStore.getState().fetchTasks();

    expect(useScheduledTaskStore.getState().tasks).toEqual([]);
    expect(useScheduledTaskStore.getState().error).toBe('加载定时任务失败');
    expect(useScheduledTaskStore.getState().loading).toBe(false);
  });

  it('viewMode=mine 时传给 service', async () => {
    mockList.mockResolvedValueOnce([]);
    useScheduledTaskStore.setState({ viewMode: 'mine' });

    await useScheduledTaskStore.getState().fetchTasks();

    expect(mockList).toHaveBeenCalledWith('mine', undefined);
  });

  it('viewMode=dept 时传 deptId', async () => {
    mockList.mockResolvedValueOnce([]);
    useScheduledTaskStore.setState({ viewMode: 'dept', viewDeptId: 'dept_xxx' });

    await useScheduledTaskStore.getState().fetchTasks();

    expect(mockList).toHaveBeenCalledWith('dept', 'dept_xxx');
  });
});

// ════════════════════════════════════════════════════════
// setViewMode
// ════════════════════════════════════════════════════════

describe('setViewMode', () => {
  it('切换视图后自动 fetchTasks', async () => {
    mockList.mockResolvedValueOnce([]);

    useScheduledTaskStore.getState().setViewMode('mine');

    expect(useScheduledTaskStore.getState().viewMode).toBe('mine');
    expect(mockList).toHaveBeenCalled();
  });

  it('切换到 dept 时存 deptId', async () => {
    mockList.mockResolvedValueOnce([]);

    useScheduledTaskStore.getState().setViewMode('dept', 'dept_ops_1');

    expect(useScheduledTaskStore.getState().viewMode).toBe('dept');
    expect(useScheduledTaskStore.getState().viewDeptId).toBe('dept_ops_1');
  });
});

// ════════════════════════════════════════════════════════
// createTask
// ════════════════════════════════════════════════════════

describe('createTask', () => {
  it('成功时乐观添加到列表', async () => {
    const newTask = makeTask({ id: 'new_t' });
    mockCreate.mockResolvedValueOnce(newTask);

    const result = await useScheduledTaskStore.getState().createTask({
      name: '新任务',
      prompt: 'x',
      cron_expr: '0 9 * * *',
      push_target: { type: 'wecom_group', chatid: 'x' },
    });

    expect(result).toEqual(newTask);
    expect(useScheduledTaskStore.getState().tasks).toContain(newTask);
  });

  it('失败时返回 null，不修改 tasks', async () => {
    mockCreate.mockRejectedValueOnce(new Error('Validation error'));

    const result = await useScheduledTaskStore.getState().createTask({
      name: 'x',
      prompt: 'x',
      cron_expr: '0 9 * * *',
      push_target: { type: 'wecom_group', chatid: 'x' },
    });

    expect(result).toBeNull();
    expect(useScheduledTaskStore.getState().tasks).toEqual([]);
  });
});

// ════════════════════════════════════════════════════════
// updateTask
// ════════════════════════════════════════════════════════

describe('updateTask', () => {
  it('成功时重新拉取并替换', async () => {
    const oldTask = makeTask({ name: '旧名' });
    const newTask = makeTask({ name: '新名' });
    useScheduledTaskStore.setState({ tasks: [oldTask] });

    mockUpdate.mockResolvedValueOnce();
    mockGet.mockResolvedValueOnce(newTask);

    const ok = await useScheduledTaskStore.getState().updateTask('t1', { name: '新名' });

    expect(ok).toBe(true);
    expect(useScheduledTaskStore.getState().tasks[0].name).toBe('新名');
  });

  it('失败时返回 false', async () => {
    mockUpdate.mockRejectedValueOnce(new Error('forbidden'));

    const ok = await useScheduledTaskStore.getState().updateTask('t1', { name: 'x' });
    expect(ok).toBe(false);
  });
});

// ════════════════════════════════════════════════════════
// deleteTask
// ════════════════════════════════════════════════════════

describe('deleteTask', () => {
  it('成功时从列表中移除', async () => {
    useScheduledTaskStore.setState({
      tasks: [makeTask({ id: 't1' }), makeTask({ id: 't2' })],
    });
    mockDelete.mockResolvedValueOnce();

    const ok = await useScheduledTaskStore.getState().deleteTask('t1');

    expect(ok).toBe(true);
    expect(useScheduledTaskStore.getState().tasks).toHaveLength(1);
    expect(useScheduledTaskStore.getState().tasks[0].id).toBe('t2');
  });

  it('删除展开中的任务时清空 expandedTaskId', async () => {
    useScheduledTaskStore.setState({
      tasks: [makeTask({ id: 't1' })],
      expandedTaskId: 't1',
    });
    mockDelete.mockResolvedValueOnce();

    await useScheduledTaskStore.getState().deleteTask('t1');

    expect(useScheduledTaskStore.getState().expandedTaskId).toBeNull();
  });
});

// ════════════════════════════════════════════════════════
// pauseTask / resumeTask / runTaskNow
// ════════════════════════════════════════════════════════

describe('pauseTask / resumeTask', () => {
  it('pauseTask 乐观更新 status=paused', async () => {
    useScheduledTaskStore.setState({
      tasks: [makeTask({ id: 't1', status: 'active' })],
    });
    mockPause.mockResolvedValueOnce();

    const ok = await useScheduledTaskStore.getState().pauseTask('t1');

    expect(ok).toBe(true);
    expect(useScheduledTaskStore.getState().tasks[0].status).toBe('paused');
  });

  it('pauseTask 失败时回滚（重新 fetch）', async () => {
    useScheduledTaskStore.setState({
      tasks: [makeTask({ id: 't1', status: 'active' })],
    });
    mockPause.mockRejectedValueOnce(new Error('forbidden'));
    mockList.mockResolvedValueOnce([makeTask({ id: 't1', status: 'active' })]);

    const ok = await useScheduledTaskStore.getState().pauseTask('t1');
    expect(ok).toBe(false);
    // 重新 fetch 会保持 active
    expect(useScheduledTaskStore.getState().tasks[0].status).toBe('active');
  });

  it('resumeTask 成功后重拉详情', async () => {
    useScheduledTaskStore.setState({
      tasks: [makeTask({ id: 't1', status: 'paused' })],
    });
    mockResume.mockResolvedValueOnce();
    mockGet.mockResolvedValueOnce(makeTask({
      id: 't1',
      status: 'active',
      next_run_at: '2026-04-12T01:00:00Z',
    }));

    const ok = await useScheduledTaskStore.getState().resumeTask('t1');

    expect(ok).toBe(true);
    expect(useScheduledTaskStore.getState().tasks[0].status).toBe('active');
    expect(useScheduledTaskStore.getState().tasks[0].next_run_at).toBe('2026-04-12T01:00:00Z');
  });

  it('runTaskNow 标记为 running', async () => {
    useScheduledTaskStore.setState({
      tasks: [makeTask({ id: 't1', status: 'active' })],
    });
    mockRunNow.mockResolvedValueOnce();

    const ok = await useScheduledTaskStore.getState().runTaskNow('t1');

    expect(ok).toBe(true);
    expect(useScheduledTaskStore.getState().tasks[0].status).toBe('running');
  });
});

// ════════════════════════════════════════════════════════
// fetchRuns
// ════════════════════════════════════════════════════════

describe('fetchRuns', () => {
  it('拉取后存入 runs[task_id]', async () => {
    const runs: TaskRun[] = [
      {
        id: 'r1',
        task_id: 't1',
        org_id: 'org_1',
        status: 'success',
        started_at: '2026-04-11T01:00:00Z',
        credits_used: 3,
        tokens_used: 1500,
      },
    ];
    mockListRuns.mockResolvedValueOnce(runs);

    await useScheduledTaskStore.getState().fetchRuns('t1');

    expect(useScheduledTaskStore.getState().runs['t1']).toEqual(runs);
  });
});

// ════════════════════════════════════════════════════════
// 乐观更新
// ════════════════════════════════════════════════════════

describe('optimisticUpdate', () => {
  it('部分字段合并', () => {
    useScheduledTaskStore.setState({
      tasks: [makeTask({ id: 't1', status: 'active', name: '旧名' })],
    });

    useScheduledTaskStore.getState().optimisticUpdate('t1', { name: '新名' });

    expect(useScheduledTaskStore.getState().tasks[0].name).toBe('新名');
    expect(useScheduledTaskStore.getState().tasks[0].status).toBe('active');
  });

  it('未匹配 id 不影响其他任务', () => {
    useScheduledTaskStore.setState({
      tasks: [makeTask({ id: 't1' }), makeTask({ id: 't2' })],
    });

    useScheduledTaskStore.getState().optimisticUpdate('t1', { name: '改了' });

    expect(useScheduledTaskStore.getState().tasks[0].name).toBe('改了');
    expect(useScheduledTaskStore.getState().tasks[1].name).toBe('测试任务');
  });
});

// ════════════════════════════════════════════════════════
// clear
// ════════════════════════════════════════════════════════

describe('clear', () => {
  it('清空所有状态', () => {
    useScheduledTaskStore.setState({
      tasks: [makeTask()],
      loading: true,
      error: 'err',
      viewMode: 'mine',
      viewDeptId: 'dept_1',
      expandedTaskId: 't1',
      runs: { t1: [] },
    });

    useScheduledTaskStore.getState().clear();

    const state = useScheduledTaskStore.getState();
    expect(state.tasks).toEqual([]);
    expect(state.loading).toBe(false);
    expect(state.error).toBeNull();
    expect(state.viewMode).toBe('default');
    expect(state.viewDeptId).toBeNull();
    expect(state.expandedTaskId).toBeNull();
    expect(state.runs).toEqual({});
  });
});
