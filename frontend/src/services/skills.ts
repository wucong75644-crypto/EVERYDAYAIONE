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

export function getAvailableSkills(conversationId: string): Promise<SkillSummary[]> {
  return request({ method: 'GET', url: '/skills/available', params: { conversation_id: conversationId } });
}

export function skillVersion(revision: string): string {
  return revision.startsWith('v') ? revision : `v${revision}`;
}
