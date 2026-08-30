/**
 * 定时任务 Service
 *
 * 后端路由: backend/api/routes/scheduled_tasks.py
 */
import api from './api';
import type {
  ScheduledTask,
  TaskRun,
  CreateTaskDto,
  UpdateTaskDto,
  ParseNLResult,
  ChatTarget,
  ScheduledTaskDraft,
  ScheduledTaskChangeRequest,
} from '../types/scheduledTask';
import type { ChangeSet } from '../types/changeset';

interface ApiResponse<T> {
  success: boolean;
  data: T;
  total?: number;
}

const BASE = '/scheduled-tasks';

export const scheduledTaskService = {
  /** 创建任务 */
  async create(dto: CreateTaskDto): Promise<ScheduledTask> {
    const res = await api.post<ApiResponse<ScheduledTask>>(BASE, dto);
    return res.data.data;
  },

  /** AI 规划并进行只读安全试跑；不会创建 active 任务。 */
  async createDraft(dto: CreateTaskDto): Promise<ScheduledTaskDraft> {
    const res = await api.post<ApiResponse<ScheduledTaskDraft>>(`${BASE}/drafts`, dto);
    return res.data.data;
  },

  /** 第二批新入口：创建 ChangeSet，不写 scheduled_task_drafts。 */
  async proposeChange(dto: ScheduledTaskChangeRequest): Promise<ChangeSet> {
    const res = await api.post<ApiResponse<ChangeSet>>(`${BASE}/changesets`, dto);
    return res.data.data;
  },

  /** 用户确认与预检配置一致后，原子启用真正的定时任务。 */
  async confirmDraft(id: string, configHash: string): Promise<ScheduledTask> {
    const res = await api.post<ApiResponse<ScheduledTask>>(`${BASE}/drafts/${id}/confirm`, {
      config_hash: configHash,
    });
    return res.data.data;
  },

  /** 列出任务（按权限自动过滤） */
  async list(view: 'default' | 'mine' | 'dept' = 'default', deptId?: string): Promise<ScheduledTask[]> {
    const params: Record<string, string> = { view };
    if (view === 'dept' && deptId) params.dept_id = deptId;
    const res = await api.get<ApiResponse<ScheduledTask[]>>(BASE, { params });
    return res.data.data;
  },

  /** 任务详情 */
  async get(id: string): Promise<ScheduledTask> {
    const res = await api.get<ApiResponse<ScheduledTask>>(`${BASE}/${id}`);
    return res.data.data;
  },

  /** 更新任务 */
  /** 修改先生成修订草稿；确认前不会改变活跃任务。 */
  async update(id: string, dto: UpdateTaskDto): Promise<ScheduledTaskDraft> {
    const res = await api.patch<ApiResponse<ScheduledTaskDraft>>(`${BASE}/${id}`, dto);
    return res.data.data;
  },

  /** 删除任务 */
  async delete(id: string): Promise<void> {
    await api.delete(`${BASE}/${id}`);
  },

  /** 暂停 */
  async pause(id: string): Promise<void> {
    await api.post(`${BASE}/${id}/pause`);
  },

  /** 恢复 */
  async resume(id: string): Promise<void> {
    await api.post(`${BASE}/${id}/resume`);
  },

  /** 立即执行 */
  async runNow(id: string): Promise<void> {
    await api.post(`${BASE}/${id}/run`);
  },

  /** 执行历史 */
  async listRuns(id: string, limit = 20): Promise<TaskRun[]> {
    const res = await api.get<ApiResponse<TaskRun[]>>(`${BASE}/${id}/runs`, {
      params: { limit },
    });
    return res.data.data;
  },

  /** 可用推送目标列表（企微群和单聊） */
  async listChatTargets(): Promise<ChatTarget[]> {
    const res = await api.get<ApiResponse<ChatTarget[]>>(`${BASE}/chat-targets`);
    return res.data.data;
  },

  /** 自然语言解析为结构化任务 */
  async parseNL(text: string): Promise<ParseNLResult> {
    const res = await api.post<ApiResponse<ParseNLResult>>(`${BASE}/parse`, { text });
    return res.data.data;
  },
};
