/** ChangeSet 对外契约：聊天消息/表单是投影，不能替代这些状态与时间线。 */

export const CHANGESET_CONTRACT_VERSION = 'changeset.v1' as const;

export type ChangeSetStatus =
  | 'draft' | 'resolving' | 'proposed' | 'validating' | 'preflighting'
  | 'awaiting_approval' | 'committing' | 'applied'
  | 'cancelled' | 'rejected' | 'failed' | 'expired' | 'conflicted';

export type ChangeSetRiskLevel = 'low' | 'medium' | 'high' | 'critical';

export interface ChangeCheck {
  id: string;
  change_set_id: string;
  check_type: string;
  check_key: string;
  input: Record<string, unknown>;
  result: Record<string, unknown>;
  status: 'pending' | 'running' | 'passed' | 'failed' | 'skipped';
  actor_id?: string | null;
  actor_type?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_at: string;
}

export interface ChangeEvent {
  id: string;
  change_set_id: string;
  sequence: number;
  event_type: string;
  from_status?: ChangeSetStatus | null;
  to_status?: ChangeSetStatus | null;
  actor_id?: string | null;
  actor_type?: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface ChangeSet {
  id: string;
  org_id: string;
  resource_type: string;
  resource_id: string;
  operation: string;
  base_revision: string;
  base_snapshot: Record<string, unknown>;
  proposed_snapshot: Record<string, unknown>;
  patch: Array<Record<string, unknown>>;
  diff: Record<string, unknown>;
  risk_level: ChangeSetRiskLevel;
  policy_snapshot: Record<string, unknown>;
  plan_snapshot?: Record<string, unknown> | null;
  tool_policy_snapshot?: Record<string, unknown> | null;
  check_summary?: Record<string, unknown> | null;
  status: ChangeSetStatus;
  idempotency_key: string;
  expires_at: string;
  created_by: string;
  created_by_type: string;
  updated_by?: string | null;
  updated_by_type?: string | null;
  audit_subject: Record<string, unknown>;
  recovery_of_id?: string | null;
  committed_revision?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  conflict?: Record<string, unknown> | null;
  revision: number;
  created_at: string;
  updated_at: string;
  checks: ChangeCheck[];
  /** 第二批稳定投影，第三批直接渲染，不需读取业务表拼接。 */
  risk?: Record<string, unknown> | null;
  plan?: Record<string, unknown> | null;
  approval_actions?: Array<{
    action: 'confirm' | 'reject' | 'cancel';
    enabled: boolean;
    method: string;
    path: string;
  }>;
  result?: Record<string, unknown> | null;
}

export interface ChangeSetTimeline {
  change_set_id: string;
  events: ChangeEvent[];
}

/**
 * 聊天消息只保存这个引用（以及可选的展示快照），不保存流程状态。
 * 该引用是 ChangeSetCard 的唯一查询入口。
 */
export interface ChangeSetMessageReference {
  type: 'changeset';
  change_set_id: string;
  title?: string;
  resource_type?: string;
  snapshot?: Record<string, unknown>;
}
