import { request } from './api';
import type { DetailProjectDraft, DetailImageCategory, DetailGenerationForm, PromptModelOption, DetailTaskSummary } from '../types/detailPage';

interface Envelope { success: boolean; data: { project: DetailProjectDraft | null } }

export const getCurrentDetailProject = async () =>
  (await request<Envelope>({ url: '/detail-projects/current' })).data.project;

export const attachDetailImage = async (workspacePath: string, category: DetailImageCategory, projectId?: string) =>
  (await request<Envelope>({ method: 'POST', url: projectId ? `/detail-projects/${projectId}/images` : '/detail-projects/current/images', data: { workspace_path: workspacePath, category } })).data.project;

export const saveDetailSettings = async (projectId: string, version: number, form: DetailGenerationForm) =>
  (await request<Envelope>({ method: 'PATCH', url: `/detail-projects/${projectId}`, data: {
    version, content_type: form.contentType, platform: form.platform, requirement: form.requirement,
    language: form.language, aspect_ratio: form.aspectRatio, quality: form.quality, image_count: form.contentType === 'default' ? 14 : form.count, prompt_model: form.promptModel ?? 'kimi-k3',
  } })).data.project;

export const removeDetailImage = async (projectId: string, imageId: string, version: number) =>
  (await request<Envelope>({ method: 'DELETE', url: `/detail-projects/${projectId}/images/${imageId}`, data: { version } })).data.project;

export const getDetailProject = async (projectId: string) =>
  (await request<Envelope>({url: `/detail-projects/${projectId}`})).data.project;
export const startDetailProject = async (projectId: string, version: number, requestId: string) =>
  (await request<Envelope>({method: 'POST',url: `/detail-projects/${projectId}/analyze`,data: {version,request_id: requestId}})).data.project;
export const archiveDetailProject = async (projectId: string) =>
  request<Envelope>({method:'POST',url:`/detail-projects/${projectId}/archive`});
export const resumeDetailPlan = async(projectId:string,planId:string,requestId:string)=>
  (await request<Envelope>({method:'POST',url:`/detail-projects/${projectId}/resume`,data:{plan_id:planId,request_id:requestId}})).data.project;
export const stopDetailRecovery = async(projectId:string)=>
  (await request<Envelope>({method:'POST',url:`/detail-projects/${projectId}/stop-recovery`})).data.project;
export const getDetailCapabilities = async () => (await request<{data:{enabled:boolean;prompt_models:PromptModelOption[];image_models:Array<{aspect_ratios:string[]}>}}>({url:'/detail-projects/capabilities'})).data;

export const createDetailProject = async (requestId: string) =>
  (await request<Envelope>({method:'POST',url:'/detail-projects',data:{request_id:requestId}})).data.project;
export const listDetailProjects = async (cursor?: string) =>
  (await request<{data:{items:DetailTaskSummary[];next_cursor:string|null}}>({url:'/detail-projects',params:cursor?{cursor}:undefined})).data;
export const refreshDetailTaskStatuses = async (ids:string[]) =>
  (await request<{data:{items:DetailTaskSummary[]}}>({url:'/detail-projects/status',params:{ids:ids.join(',')}})).data.items;
