import type { DetailGenerationForm } from './detailPage';

export interface RequirementSellingPoint {
  feature: string;
  benefit: string;
  benefit_basis: 'direct' | 'inferred';
}
export interface RequirementCreativeDirection {
  topic: string;
  text: string;
  basis: 'explicit' | 'suggested';
}
export interface RequirementSupplementQuestion {
  question: string;
  why: string;
  can_skip: true;
}
export interface RequirementAssistResult {
  product_description: string;
  selling_points: RequirementSellingPoint[];
  creative_requirements: RequirementCreativeDirection[];
  supplement_questions: RequirementSupplementQuestion[];
}
export interface RequirementRevision {
  draft: RequirementAssistResult;
  supplement: string;
  skipped_questions: string[];
}
export interface RequirementAssistMeta {
  model: string;
  fallback_used: boolean;
  latency_ms: number;
  project_version: number;
}
export interface RequirementSuggestionsEnvelope {
  success: true;
  data: RequirementAssistResult;
  error: null;
  meta: RequirementAssistMeta;
}
export interface RequirementSuggestionsRequest {
  source: { type: 'detail_project'; project_id: string };
  settings: {
    content_type: DetailGenerationForm['contentType'];
    platform: DetailGenerationForm['platform'];
    language: DetailGenerationForm['language'];
    aspect_ratio: string;
    quality: DetailGenerationForm['quality'];
    image_count: number;
    requirement: string;
  };
  revision?: RequirementRevision;
}
