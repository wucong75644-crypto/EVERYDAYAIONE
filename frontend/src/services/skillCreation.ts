import api from './api';
import type { ChangeSet } from '../types/changeset';

interface ApiResponse<T> { success: boolean; data: T }

export interface SkillCandidateEdit {
  expected_revision: number;
  name: string;
  description: string;
  body: string;
  task_modes: string[];
  triggers: string[];
  input_requirements: string[];
  open_questions: string[];
}

export interface SkillTrialEstimate {
  text_to_image: { model_id: string; image_count: number; estimated_credits: number };
  image_to_image: { model_id: string; image_count: number; estimated_credits: number };
  reference_images: Array<{ url: string; preview_url: string; name: string; message_id: string }>;
}

export interface SkillTrialRequest {
  expected_revision: number;
  content_sha256: string;
  mode: 'text' | 'image';
  input_text: string;
  idempotency_key: string;
  aspect_ratio?: string;
  reference_image_urls?: string[];
}

export interface SkillTrialResult {
  trial_id: string;
  candidate_revision: number;
  content_sha256: string;
  mode: 'text' | 'image';
  output: string;
  model_id: string;
  images?: Array<{ url: string; thumbnail_url?: string; name: string }>;
  estimated_credits?: number;
  replayed: boolean;
  feedback_rating?: 'helpful' | 'not_helpful' | null;
}

export interface SkillTrialHistory {
  enabled: boolean;
  runs: SkillTrialResult[];
}

export const skillCreationService = {
  async revise(changeSetId: string, data: SkillCandidateEdit): Promise<ChangeSet> {
    const response = await api.put<ApiResponse<ChangeSet>>(
      `/skills/authoring/proposals/${changeSetId}/revision`, data,
    );
    return response.data.data;
  },

  async estimateTrial(changeSetId: string): Promise<SkillTrialEstimate> {
    const response = await api.get<ApiResponse<SkillTrialEstimate>>(
      `/skills/authoring/proposals/${changeSetId}/trials/estimate`,
    );
    return response.data.data;
  },

  async listTrials(changeSetId: string): Promise<SkillTrialHistory> {
    const response = await api.get<ApiResponse<Array<Record<string, unknown>>> & { trial_enabled?: boolean }>(
      `/skills/authoring/proposals/${changeSetId}/trials`,
    );
    return {
      enabled: response.data.trial_enabled === true,
      runs: response.data.data.filter((row) => row.status === 'completed').map((row) => ({
        ...(row.result as Omit<SkillTrialResult, 'trial_id' | 'replayed'>),
        trial_id: String(row.id),
        candidate_revision: Number(row.candidate_revision),
        content_sha256: String(row.content_sha256),
        replayed: true,
        feedback_rating: row.feedback_rating as SkillTrialResult['feedback_rating'],
      })),
    };
  },

  async runTrial(changeSetId: string, data: SkillTrialRequest): Promise<SkillTrialResult> {
    const response = await api.post<ApiResponse<SkillTrialResult>>(
      `/skills/authoring/proposals/${changeSetId}/trials`, data,
    );
    return response.data.data;
  },

  async feedback(trialId: string, rating: 'helpful' | 'not_helpful', feedback_text = ''): Promise<void> {
    await api.put(`/skills/authoring/proposals/trials/${trialId}/feedback`, {
      rating, feedback_text,
    });
  },
};
