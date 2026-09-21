import type { DraftContent, SkillTemplateVariable } from '../../../services/skillAdmin';

export const templateSources: [SkillTemplateVariable['source'], string][] = [
  ['conversation_scope', '会话范围'], ['is_channel', '是否群组会话'],
  ['org_id', '当前组织 ID'], ['actor_user_id', '当前成员 ID'],
  ['agent_domain', '任务领域'], ['execution_mode', '执行场景'],
];
export const serverVariable = (source: SkillTemplateVariable['source']): SkillTemplateVariable =>
  ({ source, type: source === 'is_channel' ? 'boolean' : 'string' });

export function missingTemplateVariables(content: DraftContent): string[] {
  const texts = [content.body, ...(content.assets || []).filter(asset => asset.kind === 'template').map(asset => asset.content)];
  return [...new Set(texts.flatMap(text => [...text.matchAll(/\{\{args\.([a-z][a-z0-9_]{0,31})\}\}/g)].map(match => match[1])))]
    .filter(name => !Object.hasOwn(content.template_variables || {}, name));
}

export function nextAttachmentId(content: DraftContent): string {
  const used = new Set([...(content.assets || []).map(asset => asset.id),
    ...[...content.body.matchAll(/\[\[asset:([^\]]+)\]\]/g)].map(match => match[1])]);
  let number = 1;
  while (used.has(`attachment-${number}`)) number++;
  return `attachment-${number}`;
}
