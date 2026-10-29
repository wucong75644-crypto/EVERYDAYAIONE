import { request } from './api';
export type KnowledgeScope = 'personal' | 'project' | 'team';
export type KnowledgeRole = 'viewer' | 'editor' | 'manager';
export interface MapLayout { auto_balance:boolean; compact:boolean; equal_width:boolean }
export interface MapTheme { layout?:MapLayout; palette: 'none'|'ocean'|'forest'|'sunset'|'mono'; background: string; grid: boolean; font: NonNullable<CanvasStyle['font']>; font_size: number; levels: {depth:number;style:CanvasStyle}[] }
export interface KnowledgeContainer { canvas_theme?: Partial<MapTheme>; id: string; kind: KnowledgeScope; title: string; revision: number; acl_revision: number; role: KnowledgeRole }
export interface CanvasStyle { color: string; background: string; border: string; font_size: number; bold: boolean; italic: boolean; shape: 'rounded' | 'rectangle' | 'pill'; font?: 'system'|'sans'|'serif'|'mono'; underline?: boolean; strike?: boolean; border_width?: number; border_style?: 'solid'|'dashed'|'dotted' }
export interface CanvasImage { binding_id: string; revision: number; width: number; placement: 'top' | 'bottom'; aspect?: number; alt: string }
export interface CanvasVideo { binding_id:string; revision:number; title:string }
export interface CanvasLink { url:string; title:string; kind:'auto'|'video'|'web' }
export interface TitleRun { text: string; bold?: boolean; italic?: boolean; color?: string; font_size?: number }
export interface TitleFormat { runs: TitleRun[]; align: 'left'|'center'|'right' }
export interface ManualLayout {source_anchor?:RelationPoint;target_anchor?:RelationPoint}
export interface CanvasData {
  manual_layout?:ManualLayout;
  boundaries?:{id:string;members:string[];title:string}[]; title_format?: TitleFormat; width?: number | null; height?: number | null; structure?: import("../components/knowledge/topicStructure").TopicStructure; video?:CanvasVideo|null; link?:CanvasLink|null; style?: CanvasStyle | null; image?: CanvasImage | null; note?: string; labels?: string[]; task?: { progress: number; priority: number; start: string; due: string; assignee: string } | null; summary?: { members: string[]; title: string } | null; boundary?: { members: string[]; title: string } | null }
export interface KnowledgeNode { has_body?:boolean;attachments?:{total:number;items:KnowledgeFile[]}; canvas_data?: CanvasData; id: string; container_id: string; parent_id: string | null; kind: 'topic' | 'document' | 'rule'; title: string; revision: number; node_id: string; x: number; y: number; layout_revision: number; sort_order?: number | null }
export interface DerivedSection {id:string;title:string;text:string;source_refs:string[];kind:string;manual?:boolean;verification?:'manual'|'verified'|'extractive'|'navigation'|'source'|'unverified'}
export interface DerivedKnowledge {generation_id:string|null;sections:DerivedSection[];tags:string[];auto_tags?:string[];status:string;coverage?:string;overrides?:Record<string,{text?:string;hidden?:boolean}>;override_revisions?:Record<string,number>;source_links?:Record<string,string>}
export interface DirectoryDocument extends KnowledgeObject {derived:DerivedKnowledge|null}
export interface ContentPlan {title:string;body:string;children:{title:string;body:string}[]}
export interface KnowledgeObject extends Pick<KnowledgeNode, 'id' | 'container_id' | 'parent_id' | 'kind' | 'title' | 'revision'> { body: string; can_write: boolean }
export type RelationPoint = {x:number;y:number};
export type RelationAppearance = {text:string;bend:number;source_anchor?:RelationPoint|null;target_anchor?:RelationPoint|null;source_control?:RelationPoint|null;target_control?:RelationPoint|null};
export interface KnowledgeRelation { id: string; container_id: string; source_object_id: string; target_object_id: string; relation_type: string; appearance?: RelationAppearance }
export interface KnowledgeScene { containers: KnowledgeContainer[]; nodes: KnowledgeNode[]; relations: KnowledgeRelation[] }
export interface CanvasHistoryEntry { container_id?: string; container_title?: string; container_kind?: KnowledgeScope; history_id: string; operation: string; can_apply: boolean; reason: string }
export interface CanvasHistory { undo: CanvasHistoryEntry | null; redo: CanvasHistoryEntry | null }
interface Page extends KnowledgeScene { next_cursor: string | null }
export interface KnowledgeMember { user_id: string; nickname: string; org_role: string; role: KnowledgeRole | null; status: 'active' | 'removed' | null }
export interface KnowledgeVersion { revision: number; title: string; created_at: string }
export interface KnowledgeVersionDetail extends KnowledgeVersion { object_id: string; body: string }
export interface KnowledgeSearchHit { id: string; title: string; kind: KnowledgeNode['kind']; revision: number; excerpt: string }
export interface KnowledgeResults<T, C = string> { items: T[]; next_cursor: C | null }
interface Envelope<T> { success: true; data: T }
async function get<T>(url: string, signal?: AbortSignal) { return (await request<Envelope<T>>({ url, signal })).data; }
export const knowledgeApi = {
  contentPlan:async(payload:{container_id:string;object_id?:string;expected_revision?:number;text:string})=>(await request<Envelope<ContentPlan>>({url:'/knowledge/content-plan',method:'POST',data:payload,timeout:120000})).data,
  document:(id:string,signal?:AbortSignal,generation?:string)=>get<DirectoryDocument>('/knowledge/objects/'+id+'/document'+(generation?'?generation='+encodeURIComponent(generation):''),signal),
  override:(id:string,key:string,value:{text?:string;hidden?:boolean},revision:number,commandId:string)=>request<Envelope<{revision:number}>>({url:'/knowledge/objects/'+id+'/knowledge-overrides',method:'POST',data:{key,value,expected_revision:revision,command_id:commandId}}),
  captureSnapshot: (payload: Record<string, unknown>) => request<Envelope<{ snapshot_id: string; nodes: KnowledgeNode[] }>>({ url: '/knowledge/branch-snapshots', method: 'POST', data: payload }),
  history: (signal?: AbortSignal) => get<CanvasHistory>('/knowledge/canvas-history', signal),
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
  versions: (id: string, before?: number, signal?: AbortSignal) => get<KnowledgeResults<KnowledgeVersion, number>>('/knowledge/objects/' + id + '/versions' + (before ? '?before=' + before : ''), signal),
  version: (id: string, revision: number, signal?: AbortSignal) => get<KnowledgeVersionDetail>('/knowledge/objects/' + id + '/versions/' + revision, signal),
  search: (id: string, query: string, cursor?: string, signal?: AbortSignal) => get<KnowledgeResults<KnowledgeSearchHit>>('/knowledge/containers/' + id + '/search?' + new URLSearchParams({ q: query, ...(cursor ? { cursor } : {}) }), signal),
  members: (id: string) => get<KnowledgeMember[]>('/knowledge/containers/' + id + '/members'),
  command: (operation: string, payload: Record<string, unknown>, commandId: string = crypto.randomUUID()) =>
    request<Envelope<{ object_id: string | null }>>({ url: '/knowledge/commands', method: 'POST', data: { command_id: commandId, operation, payload } }),
};

export interface KnowledgeFileVersion {
  ai_processing_enabled?:boolean;parse_state?:string; binding_id: string; revision: number; version_id: string; asset_id: string; name: string;
  mime_type: string; size: number; sha256: string; text_status: 'ready' | 'unsupported' | 'failed' | 'truncated'; created_at: string;
}
export interface KnowledgeFile { id: string; title: string; current_revision: number; version: KnowledgeFileVersion }
export interface KnowledgeFileTarget { binding_id?: string; expected_revision?: number }
export interface XmindImportResult { status:string; container_id:string; object_id:string; folded_ids:string[]; hidden?:boolean; report:{topics:number;images:number;links:number;summaries:number;warnings:string[]} }
export interface ImportProgress {ai_processing_enabled?:boolean;framework:string;nodes:number;files:Record<string,number>;links_unread:number}
export const knowledgeFilesApi = {
  progress:(containerId:string,signal?:AbortSignal)=>get<ImportProgress>('/knowledge/containers/'+containerId+'/import-progress',signal),
  retry:(objectId:string,bindingId:string,revision:number)=>request({url:'/knowledge/objects/'+objectId+'/files/'+bindingId+'/versions/'+revision+'/retry',method:'POST'}),
  importXmind: (file:File, commandId:string, containerId:string, expectedRevision:number) => {
    const data=new FormData(); data.append('file',file); data.append('command_id',commandId); data.append('container_id',containerId); data.append('expected_container_revision',String(expectedRevision));
    return request<Envelope<XmindImportResult>>({url:'/knowledge/imports/xmind',method:'POST',data,timeout:120000});
  },
  list: (objectId: string, signal?: AbortSignal) => get<KnowledgeFile[]>('/knowledge/objects/' + objectId + '/files', signal),
  versions: (objectId: string, bindingId: string, before?: number, signal?: AbortSignal) => get<KnowledgeResults<KnowledgeFileVersion, number>>('/knowledge/objects/' + objectId + '/files/' + bindingId + '/versions' + (before ? '?before=' + before : ''), signal),
  upload: (objectId: string, file: File, commandId: string, target: KnowledgeFileTarget = {}) => {
    const data = new FormData(); data.append('file', file); data.append('command_id', commandId);
    if (target.binding_id) data.append('binding_id', target.binding_id);
    if (target.expected_revision) data.append('expected_revision', String(target.expected_revision));
    return request<Envelope<unknown>>({ url: '/knowledge/objects/' + objectId + '/files', method: 'POST', data, timeout: 120000 });
  },
  snapshot: (objectId: string, path: string, commandId: string) => request<Envelope<unknown>>({ url: '/knowledge/objects/' + objectId + '/files/from-workspace', method: 'POST', data: { command_id: commandId, workspace_path: path }, timeout: 120000 }),
  reference: (objectId: string, sourceObject: string, sourceBinding: string, revision: number, commandId: string) => request<Envelope<unknown>>({ url: '/knowledge/objects/' + objectId + '/files/reference', method: 'POST', data: { command_id: commandId, source_object_id: sourceObject, source_binding_id: sourceBinding, source_revision: revision } }),
  remove: (objectId: string, binding: KnowledgeFile, commandId: string) => request<Envelope<unknown>>({ url: '/knowledge/objects/' + objectId + '/files/' + binding.id, method: 'DELETE', data: { command_id: commandId, expected_revision: binding.current_revision } }),
  thumbnail: (objectId:string,bindingId:string,revision:number,signal?:AbortSignal)=>request<Blob>({url:'/knowledge/objects/'+objectId+'/files/'+bindingId+'/versions/'+revision+'/thumbnail',responseType:'blob',signal,timeout:30000}),
  playback: (objectId:string,bindingId:string,revision:number,signal?:AbortSignal,quality:'preview'|'original'='preview')=>request<Envelope<{url:string;expires_in:number}>>({url:'/knowledge/objects/'+objectId+'/files/'+bindingId+'/versions/'+revision+'/playback?quality='+quality,method:'POST',signal,timeout:60000}).then(result=>result.data),
  content: (objectId: string, bindingId: string, revision: number, signal?: AbortSignal) => request<Blob>({ url: '/knowledge/objects/' + objectId + '/files/' + bindingId + '/versions/' + revision + '/content', responseType: 'blob', signal, timeout: 120000 }),
};
