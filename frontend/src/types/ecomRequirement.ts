import type { DetailGenerationForm } from './detailPage';

export type RequirementSuggestionId = 'selling_point' | 'scene' | 'creative';
export type ReferencePrimaryUse =
  | 'background'
  | 'composition'
  | 'color'
  | 'lighting'
  | 'texture'
  | 'typography'
  | 'rhythm';

export interface RequirementProductFacts {
  product_name: string;
  confirmed_attributes: string[];
  unclear_items: string[];
}

export interface RequirementReferenceAnalysis {
  image_id: string;
  primary_uses: ReferencePrimaryUse[];
  summary: string;
  excluded_elements: string[];
}

export interface RequirementConflict {
  field: string;
  user_value: string;
  confirmed_value: string;
  message: string;
  blocked_claims: string[];
}

export interface RequirementSuggestion {
  id: RequirementSuggestionId;
  name: string;
  style_name: string;
  brief_markdown: string;
}

export interface RequirementAssistResult {
  product_facts: RequirementProductFacts;
  reference_analyses: RequirementReferenceAnalysis[];
  conflicts: RequirementConflict[];
  suggestions: RequirementSuggestion[];
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
}
