import api from './api';
import type { ImagePart } from '../types/message';

export interface ChatImageInput {
  schema_version: number;
  prompt: string; prompt_sha256: string; request_hash: string;
  mode: 'text_to_image' | 'image_to_image'; model: string;
  aspect_ratio: string; resolution: string | null; output_format: string;
  estimated_credits: number; estimated_provider_credits: number;
  background?: 'opaque' | 'transparent';
  references: Array<{ role: string; workspace_path: string; content_sha256: string; size: number;
    asset_id?: string; message_id?: string; content_index?: number; resource_ref?: string; file_id?: string }>;
  origin: { retry_of_task_id?: string; parent_task_id: string; [key: string]: unknown };
  budget: { max_requests: number; max_credits: number };
  source_prompt?: Record<string, unknown>;
  plan_item_id?: string; variant_id?: string;
}
export interface ChatImageDetails {
  task_id: string; message_id: string; status: string; submission_state: string;
  input: ChatImageInput; result?: ImagePart[]; credits_used: number;
  reference_previews?: Array<{ index: number; url: string | null; available: boolean }>;
  can_stop: boolean; can_replay: boolean; cancel_explanation: string;
  feedback?: { rating: 'helpful' | 'not_helpful' };
  platform_cost?: { reason: string; refunded_user_credits: number; estimated_provider_credits: number; evidence: string };
}
export interface ChatImageEstimate {
  per_image_credits: number; total_credits: number; image_count: number;
  acceptance_enabled: boolean; within_budget: boolean; max_requests: number; max_credits: number;
}
export interface ChatImageReceipt {
  task_id: string; message_id: string; conversation_id: string;
  status: 'submitted'; submission_state: string; estimated_credits: number;
}
export const chatImageService = {
  async estimate(input: ChatImageInput): Promise<ChatImageEstimate> {
    return (await api.post<ChatImageEstimate>('/tasks/image/estimate', {
      mode: input.mode, model: input.model, aspect_ratio: input.aspect_ratio,
      resolution: input.resolution, output_format: input.output_format,
      ...(input.background ? { background: input.background } : {}),
      reference_count: input.references.length, image_count: 1,
    })).data;
  },
  async details(taskId: string): Promise<ChatImageDetails> {
    return (await api.get<ChatImageDetails>(`/tasks/${taskId}/image`)).data;
  },
  async replay(taskId: string, requestId: string): Promise<ChatImageReceipt> {
    return (await api.post<ChatImageReceipt>(`/tasks/${taskId}/image/replay`, { request_id: requestId })).data;
  },
  async stop(taskId: string): Promise<{ outcome: string; message?: string }> {
    return (await api.post(`/tasks/${taskId}/image/stop`)).data;
  },
  async feedback(taskId: string, rating: 'helpful' | 'not_helpful'): Promise<void> {
    await api.put(`/tasks/${taskId}/image/feedback`, { rating });
  },
};
