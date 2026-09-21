import type { DraftContent, SkillDetail, SkillState } from '../../../services/skillAdmin';

export const stateLabels = { draft: '草稿', in_review: '待审核', published: '已发布', deprecated: '已废弃', disabled: '已停用', retired: '已撤销' };
export const emptyContent = (): DraftContent => ({ description: '', body: '', catalog_metadata: {} });
export const contentName = (content?: DraftContent | null) => typeof content?.catalog_metadata.name === 'string' ? content.catalog_metadata.name : '';
export const detailName = (detail: SkillDetail) => (detail.draft ? contentName(detail.draft.content)
  : typeof detail.revisions[0]?.catalog_metadata.name === 'string' ? detail.revisions[0].catalog_metadata.name : '') || detail.skill_key;
export const detailState = (detail: SkillDetail): SkillState => detail.draft?.status
  ?? (detail.revisions[0]?.status === 'retired' ? 'disabled' : detail.revisions[0]?.status) ?? 'draft';
export const revisionLabel = (detail: SkillDetail, revision: string) => {
  const index = detail.revisions.findIndex(row => row.revision === revision);
  return index < 0 ? revision : `第 ${detail.revisions.length - index} 版`;
};
export const formatDate = (value?: string | null) => value && !Number.isNaN(Date.parse(value))
  ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }) : '—';
export interface SkillNavigationState { dirty: boolean; busy: boolean }
