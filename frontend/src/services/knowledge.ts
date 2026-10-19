import { request } from './api';
export type KnowledgeScope = 'personal' | 'project' | 'team';
export type KnowledgeRole = 'viewer' | 'editor' | 'manager';
export interface KnowledgeContainer { id: string; kind: KnowledgeScope; title: string; revision: number; acl_revision: number; role: KnowledgeRole }
export interface KnowledgeNode { id: string; container_id: string; parent_id: string | null; kind: 'topic' | 'document' | 'rule'; title: string; revision: number; node_id: string; x: number; y: number; layout_revision: number }
export interface KnowledgeObject extends Pick<KnowledgeNode, 'id' | 'container_id' | 'parent_id' | 'kind' | 'title' | 'revision'> { body: string; can_write: boolean }
export interface KnowledgeRelation { id: string; container_id: string; source_object_id: string; target_object_id: string; relation_type: string }
export interface KnowledgeScene { containers: KnowledgeContainer[]; nodes: KnowledgeNode[]; relations: KnowledgeRelation[] }
interface Page extends KnowledgeScene { next_cursor: string | null }
export interface KnowledgeMember { user_id: string; nickname: string; org_role: string; role: KnowledgeRole | null; status: 'active' | 'removed' | null }
interface Envelope<T> { success: true; data: T }
async function get<T>(url: string, signal?: AbortSignal) { return (await request<Envelope<T>>({ url, signal })).data; }
export const knowledgeApi = {
  available: (signal?: AbortSignal) => get<{ enabled: boolean }>('/knowledge/availability', signal),
  initialize: () => request<Envelope<null>>({ url: '/knowledge/initialize', method: 'POST' }),
  async scene(signal?: AbortSignal): Promise<KnowledgeScene> {
    const nodes: KnowledgeNode[] = []; let cursor: string | null = null;
    do {
      const page: Page = await get<Page>('/knowledge/canvas' + (cursor ? '?cursor=' + cursor : ''), signal);
      nodes.push(...page.nodes); cursor = page.next_cursor;
      if (!cursor) return { containers: page.containers, nodes, relations: page.relations };
      if (nodes.length >= 10000) throw new Error('画布超过当前预览容量，请缩小空间后重试');
    } while (cursor);
    throw new Error('未能加载画布');
  },
  object: (id: string) => get<KnowledgeObject>('/knowledge/objects/' + id),
  members: (id: string) => get<KnowledgeMember[]>('/knowledge/containers/' + id + '/members'),
  command: (operation: string, payload: Record<string, unknown>, commandId: string = crypto.randomUUID()) =>
    request<Envelope<{ object_id: string | null }>>({ url: '/knowledge/commands', method: 'POST', data: { command_id: commandId, operation, payload } }),
};
