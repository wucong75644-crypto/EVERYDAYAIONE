import { request } from './api';

export type SkillState = 'draft' | 'in_review' | 'published' | 'deprecated' | 'disabled';
export type SkillAction = 'start_draft' | 'submit' | 'approve' | 'reject' | 'publish' | 'deprecate' | 'disable';
export interface DraftContent {
  description: string;
  body: string;
  catalog_metadata: Record<string, unknown>;
}
export interface SkillAdminItem {
  package_id: string;
  skill_key: string;
  scope_kind: 'platform' | 'org';
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
  scope_kind: 'platform' | 'org';
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
const base = (orgId: string) => `/skills/admin/orgs/${encodeURIComponent(orgId)}`;
export const listManagedSkills = (orgId: string): Promise<SkillAdminItem[]> =>
  request({ method: 'GET', url: base(orgId) });
export const getManagedSkill = (orgId: string, id: string): Promise<SkillDetail> =>
  request({ method: 'GET', url: `${base(orgId)}/${id}` });
export const createManagedSkill = (orgId: string, skill_key: string, content?: DraftContent): Promise<{ package_id: string }> =>
  request({ method: 'POST', url: base(orgId), data: { skill_key, ...(content ? { content } : {}) } });
export const saveSkillDraft = (orgId: string, id: string, expected_version: number, content: DraftContent): Promise<SkillDetail> =>
  request({ method: 'PUT', url: `${base(orgId)}/${id}/draft`, data: { expected_version, content } });
export const transitionSkill = (orgId: string, id: string, expected_version: number, action: SkillAction): Promise<SkillDetail> =>
  request({ method: 'POST', url: `${base(orgId)}/${id}/transitions`, data: { expected_version, action } });
export const readSkillRevision = (orgId: string, id: string, revision: string): Promise<DraftContent> =>
  request({ method: 'GET', url: `${base(orgId)}/${id}/revisions/${encodeURIComponent(revision)}` });
