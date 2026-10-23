import { request } from './api';
export interface KnowledgeReference { root: { kind: 'object' | 'container'; id: string }; policy: 'latest_saved' }
export interface KnowledgeRootCandidate { kind: 'object' | 'container'; id: string; title: string; scope_title: string }
export async function searchKnowledgeRoots(query: string): Promise<KnowledgeRootCandidate[]> {
  const result = await request<{success: boolean; data: {items: KnowledgeRootCandidate[]}}>({url: '/knowledge/roots', params: {query}});
  return result.data.items;
}
