/**
 * 定时任务类型定义
 *
 * 设计文档: docs/document/UI_定时任务面板设计.md
 * 后端 schemas: backend/api/routes/scheduled_tasks.py
 */
import type { PositionCode, DepartmentType } from './auth';

export type TaskStatus = 'active' | 'paused' | 'error' | 'running';

export interface PushTarget {
  type: 'wecom_group' | 'wecom_user' | 'web' | 'multi';
  chatid?: string;
  chat_name?: string;
  wecom_userid?: string;
  name?: string;
  user_id?: string;
  conversation_id?: string;
  targets?: PushTarget[];
}

export interface TemplateFile {
  path: string;
  name: string;
  url?: string;
}

export interface ScheduledTaskCreator {
  name: string;
  avatar?: string | null;
  department_id?: string | null;
  department_name?: string | null;
  department_type?: DepartmentType | null;
  position_code?: PositionCode | null;
}

export interface TaskRunResult {
  status?: 'success' | 'failed';
  tokens?: number;
  turns?: number;
  files?: Array<{ url: string; name: string; mime?: string; size?: number }>;
}

export interface ScheduledTask {
  id: string;
  org_id: string;
  user_id: string;
  creator?: ScheduledTaskCreator;

  name: string;
  prompt: string;
  cron_expr: string;
  cron_readable?: string;
  timezone: string;

  push_target: PushTarget;
  template_file?: TemplateFile | null;

  status: TaskStatus;
  max_credits: number;
  retry_count: number;
  timeout_sec: number;

  last_summary?: string | null;
  last_result?: TaskRunResult | null;

  next_run_at?: string | null;
  last_run_at?: string | null;
  run_count: number;
  consecutive_failures: number;

  created_at: string;
  updated_at: string;
}

export interface TaskRun {
  id: string;
  task_id: string;
  org_id: string;
  status: 'running' | 'success' | 'failed' | 'timeout' | 'skipped';
  started_at: string;
  finished_at?: string | null;
  duration_ms?: number | null;
  result_summary?: string | null;
  result_files?: Array<{ url: string; name: string }> | null;
  push_status?: 'pushed' | 'push_failed' | 'skipped' | null;
  error_message?: string | null;
  credits_used: number;
  tokens_used: number;
}

export interface CreateTaskDto {
  name: string;
  prompt: string;
  cron_expr: string;
  timezone?: string;
  push_target: PushTarget;
  template_file?: TemplateFile | null;
  max_credits?: number;
  retry_count?: number;
  timeout_sec?: number;
}

export interface UpdateTaskDto {
  name?: string;
  prompt?: string;
  cron_expr?: string;
  timezone?: string;
  push_target?: PushTarget;
  template_file?: TemplateFile | null;
  max_credits?: number;
  retry_count?: number;
  timeout_sec?: number;
}

export interface ParseNLResult {
  name: string;
  prompt: string;
  cron_expr: string;
  cron_readable: string;
  suggested_target: PushTarget | null;
}

export interface ChatTarget {
  chatid: string;
  chat_type: 'group' | 'single';
  chat_name?: string | null;
  last_active?: string | null;
}
