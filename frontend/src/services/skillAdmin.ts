import { request } from './api';

export type SkillState = 'draft' | 'in_review' | 'published' | 'deprecated' | 'disabled';
export type SkillAction = 'start_draft' | 'submit' | 'approve' | 'reject' | 'publish' | 'publish_private' | 'deprecate' | 'disable' | 'enable';
export type SkillScope = 'personal' | 'org' | 'platform';
export type SkillOwner = { kind: 'personal' } | { kind: 'platform' } | { kind: 'org'; orgId: string };
export interface SkillAssetSummary {
  id: string; name: string; summary: string;
  kind: 'reference' | 'template' | 'example_input' | 'example_output';
  format: 'md' | 'txt' | 'json' | 'csv';
  bytes?: number;
  file_format?: SkillFileFormat;
  file_bytes?: number;
}
export type SkillFileFormat = 'md' | 'txt' | 'json' | 'csv' | 'docx' | 'pdf' | 'xlsx';
export interface SkillAssetDraft extends SkillAssetSummary {
  content: string;
  source?: { format: SkillFileFormat; base64: string } | null;
}
export interface SkillTemplateVariable {
  type: 'string' | 'boolean';
  source: 'actor_user_id' | 'org_id' | 'conversation_scope' | 'agent_domain' | 'execution_mode' | 'is_channel';
}
export interface DraftContent {
  description: string;
  body: string;
  catalog_metadata: Record<string, unknown>;
  assets?: SkillAssetDraft[];
  asset_summaries?: SkillAssetSummary[];
  template_variables?: Record<string, SkillTemplateVariable>;
}
export interface SkillAdminItem {
  package_id: string;
  skill_key: string;
  scope_kind: SkillScope;
  status: SkillState;
  version: number | null;
  published_revision: string | null;
  description: string | null;
  approved: boolean;
  name?: string | null;
  working_description?: string | null;
  updated_at?: string | null;
  available_revision?: string | null;
  available_revision_number?: number | null;
}
export interface SkillDetail {
  package_id: string;
  skill_key: string;
  scope_kind: SkillScope;
  editable: boolean;
  available_revision?: string | null;
  draft: {
    status: SkillState;
    version: number;
    revision: string;
    content: DraftContent;
    approved_by: string | null;
    approved_at: string | null;
    updated_at: string;
  } | null;
  revisions: {
    revision: string;
    summary: string;
    status: 'published' | 'deprecated' | 'disabled' | 'retired';
    catalog_metadata: Record<string, unknown>;
    created_at: string;
  }[];
}

// Explicit organization in the URL survives an organization switch during an
// in-flight operation. The server revalidates membership for this exact target.
const ownerOf = (owner: SkillOwner | string): SkillOwner => typeof owner === 'string' ? { kind: 'org', orgId: owner } : owner;
const base = (owner: SkillOwner | string) => {
  const value = ownerOf(owner);
  return value.kind === 'org' ? `/skills/admin/orgs/${encodeURIComponent(value.orgId)}` : `/skills/admin/${value.kind}`;
};
export const importSkillAttachment = (owner: SkillOwner | string, file: File): Promise<SkillAssetDraft> => {
  const data = new FormData();
  data.append('file', file);
  return request({ method: 'POST', url: `${base(owner)}/attachments/import`, data });
};
export const listManagedSkills = (owner: SkillOwner | string): Promise<SkillAdminItem[]> =>
  request({ method: 'GET', url: base(owner) });
export const getManagedSkill = (owner: SkillOwner | string, id: string): Promise<SkillDetail> =>
  request({ method: 'GET', url: `${base(owner)}/${id}` });
export const createManagedSkill = (owner: SkillOwner | string, skill_key: string, content?: DraftContent): Promise<{ package_id: string }> =>
  request({ method: 'POST', url: base(owner), data: { skill_key, ...(content ? { content } : {}) } });
export const saveSkillDraft = (owner: SkillOwner | string, id: string, expected_version: number, content: DraftContent): Promise<SkillDetail> =>
  request({ method: 'PUT', url: `${base(owner)}/${id}/draft`, data: { expected_version, content } });
export const transitionSkill = (owner: SkillOwner | string, id: string, expected_version: number, action: SkillAction): Promise<SkillDetail> =>
  request({ method: 'POST', url: `${base(owner)}/${id}/transitions`, data: { expected_version, action } });
export const readSkillRevision = (owner: SkillOwner | string, id: string, revision: string): Promise<DraftContent> =>
  request({ method: 'GET', url: `${base(owner)}/${id}/revisions/${encodeURIComponent(revision)}` });

export interface SkillDeletionCheck {
  allowed: boolean;
  reason: string | null;
  blocking_tasks: number;
  uncertain_tasks: number;
}
export const checkSkillDeletion = (owner: SkillOwner | string, id: string): Promise<SkillDeletionCheck> =>
  request({ method: 'GET', url: `${base(owner)}/${id}/deletion-check` });
export const deleteManagedSkill = (owner: SkillOwner | string, id: string, expected_version: number): Promise<{ package_id: string; deleted: boolean }> =>
  request({ method: 'DELETE', url: `${base(owner)}/${id}`, data: { expected_version } });
