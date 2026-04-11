/**
 * 定时任务类型定义
 *
 * 设计文档: docs/document/UI_定时任务面板设计.md
 * 后端 schemas: backend/api/routes/scheduled_tasks.py
 */
import type { PositionCode, DepartmentType } from './auth';

export type TaskStatus = 'active' | 'paused' | 'error' | 'running';
export type ScheduleType = 'once' | 'daily' | 'weekly' | 'monthly' | 'cron';

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
  // 频率字段（V2: 结构化）
  schedule_type: ScheduleType;
  cron_expr: string | null;
  cron_readable?: string;
  weekdays?: number[] | null;       // weekly: [0=日, 1=一, ..., 6=六]
  day_of_month?: number | null;     // monthly
  run_at?: string | null;           // once: ISO 8601
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
  timezone?: string;
  push_target: PushTarget;
  template_file?: TemplateFile | null;
  max_credits?: number;
  retry_count?: number;
  timeout_sec?: number;
  // 频率字段
  schedule_type: ScheduleType;
  cron_expr?: string;
  time_str?: string;        // "HH:MM"
  weekdays?: number[];
  day_of_month?: number;
  run_at?: string;          // ISO 8601 (含时区)
}

export interface UpdateTaskDto {
  name?: string;
  prompt?: string;
  timezone?: string;
  push_target?: PushTarget;
  template_file?: TemplateFile | null;
  max_credits?: number;
  retry_count?: number;
  timeout_sec?: number;
  // 频率字段
  schedule_type?: ScheduleType;
  cron_expr?: string;
  time_str?: string;
  weekdays?: number[];
  day_of_month?: number;
  run_at?: string;
}

export interface ParseNLResult {
  name: string;
  prompt: string;
  schedule_type: ScheduleType;
  cron_expr?: string | null;
  time_str?: string | null;
  weekdays?: number[] | null;
  day_of_month?: number | null;
  run_at?: string | null;
  cron_readable?: string | null;
  suggested_target: PushTarget | null;
}

export interface ChatTarget {
  chatid: string;
  chat_type: 'group' | 'single';
  chat_name?: string | null;
  last_active?: string | null;
}
