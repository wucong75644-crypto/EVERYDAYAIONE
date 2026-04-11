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
} from '../types/scheduledTask';

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
  async update(id: string, dto: UpdateTaskDto): Promise<void> {
    await api.patch(`${BASE}/${id}`, dto);
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
