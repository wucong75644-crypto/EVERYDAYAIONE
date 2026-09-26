import { request } from './api';

export interface SkillSelection {
  skill_id: string;
  revision: string;
}

export interface SkillSummary extends SkillSelection {
  name: string;
  description: string;
  triggers: string[];
  source: 'platform' | 'org';
  model_selectable: boolean;
}

export interface SkillBinding extends SkillSummary {
  binding_id: string;
  available: boolean;
}

const bindingUrl = (conversationId: string) =>
  `/skills/conversations/${encodeURIComponent(conversationId)}/bindings`;

export function getSkillBindings(conversationId: string): Promise<SkillBinding[]> {
  return request({ method: 'GET', url: bindingUrl(conversationId) });
}

export function addSkillBinding(conversationId: string, skill: SkillSelection): Promise<{ binding_id: string }> {
  return request({ method: 'POST', url: bindingUrl(conversationId),
    data: { skill_id: skill.skill_id, revision: skill.revision } });
}

export function removeSkillBinding(conversationId: string, bindingId: string): Promise<void> {
  return request({ method: 'DELETE', url: `${bindingUrl(conversationId)}/${encodeURIComponent(bindingId)}` });
}

export function getAvailableSkills(conversationId: string): Promise<SkillSummary[]> {
  return request({ method: 'GET', url: '/skills/available', params: { conversation_id: conversationId } });
}

export function skillVersion(revision: string): string {
  return revision.startsWith('v') ? revision : `v${revision}`;
}

export type SkillFileType = 'pdf' | 'docx' | 'xlsx' | 'csv' | 'pptx' | 'image' | 'text';
export interface SkillRecommendation extends SkillSummary {
  reasons: { code: 'organization' | 'domain' | 'execution_mode' | 'tools' | 'file_type' | 'session_binding'; values: string[] }[];
}
export interface SkillRecommendationBatch {
  status: 'ready' | 'disabled' | 'unavailable';
  recommendation_id: string | null;
  candidates: SkillRecommendation[];
}
export function getSkillRecommendations(conversationId: string, fileTypes: SkillFileType[], permissionMode: 'auto' | 'ask' | 'plan'): Promise<SkillRecommendationBatch> {
  return request({ method: 'POST', url: '/skills/recommendations', data: {
    conversation_id: conversationId, selected_file_types: fileTypes, permission_mode: permissionMode,
  } });
}
export function sendSkillRecommendationFeedback(conversationId: string, recommendationId: string,
  skill: SkillSelection, feedback: 'selected' | 'dismissed' | 'not_relevant'): Promise<void> {
  return request({ method: 'POST', url: `/skills/recommendations/${encodeURIComponent(recommendationId)}/feedback`,
    data: { conversation_id: conversationId, skill_id: skill.skill_id, revision: skill.revision, feedback } });
}
