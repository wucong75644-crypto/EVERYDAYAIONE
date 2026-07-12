import { request } from './api';
import type { DetailProjectDraft, DetailImageCategory, DetailGenerationForm } from '../types/detailPage';

interface Envelope { success: boolean; data: { project: DetailProjectDraft | null } }

export const getCurrentDetailProject = async () =>
  (await request<Envelope>({ url: '/detail-projects/current' })).data.project;

export const attachDetailImage = async (workspacePath: string, category: DetailImageCategory) =>
  (await request<Envelope>({ method: 'POST', url: '/detail-projects/current/images', data: { workspace_path: workspacePath, category } })).data.project;

export const saveDetailSettings = async (projectId: string, version: number, form: DetailGenerationForm) =>
  (await request<Envelope>({ method: 'PATCH', url: `/detail-projects/${projectId}`, data: {
    version, content_type: form.contentType, platform: form.platform, requirement: form.requirement,
    language: form.language, aspect_ratio: form.aspectRatio, quality: form.quality, image_count: form.count,
  } })).data.project;

export const removeDetailImage = async (projectId: string, imageId: string, version: number) =>
  (await request<Envelope>({ method: 'DELETE', url: `/detail-projects/${projectId}/images/${imageId}`, data: { version } })).data.project;
