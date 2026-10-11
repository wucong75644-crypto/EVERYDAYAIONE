export type DetailPageStep = 1 | 2 | 3 | 4 | 5;
export type DetailContentType = 'default' | 'main_image' | 'detail_page';
export type DetailImageCategory = 'product' | 'reference';
export type DetailItemStatus = 'waiting' | 'generating' | 'completed' | 'failed';
export type DetailImageStatus = 'local' | 'uploading' | 'attaching' | 'ready' | 'failed' | 'missing';

export interface DetailLocalImage {
  id: string;
  category: DetailImageCategory;
  file?: File;
  previewUrl: string;
  originalUrl?: string;
  error: string | null;
  workspacePath?: string;
  status: DetailImageStatus;
  sortOrder?: number;
  name: string;
}

export interface DetailProjectDraft {
  id: string;
  title?: string;
  created_at?: string;
  version: number;
  content_type: DetailContentType;
  platform: DetailGenerationForm['platform'];
  requirement: string;
  language: DetailGenerationForm['language'];
  aspect_ratio: string;
  quality: DetailGenerationForm['quality'];
  image_count: number;
  status?: string;
  prompt_model?: PromptModel;
  groups?: DetailGroup[];
  runs?: DetailGenerationRun[];
  run_state?: {run_id?: string; request_id?: string; delivery_stopped?: boolean};
  images: Array<{
    id: string; category: DetailImageCategory; workspace_path: string; sort_order: number;
    status: 'ready' | 'missing'; original_url: string | null; thumbnail_url: string | null;
  }>;
}

export interface DetailGenerationForm {
  contentType: DetailContentType;
  promptModel?: PromptModel;
  platform: 'auto' | 'taobao' | 'tmall' | 'jd' | 'pdd';
  requirement: string;
  language: 'zh-CN' | 'none';
  aspectRatio: string;
  quality: '1k' | '2k' | '4k';
  count: number;
}

export interface DetailPlanItem {
  id: string;
  role: string;
  purpose: string;
  composition: string;
  title: string;
  subtitle: string;
  prompt: string;
  aspectRatio: string;
  hasText: boolean;
}

export interface DetailGenerationItem extends DetailPlanItem {
  status: DetailItemStatus;
  previewUrl: string | null;
  error: string | null;
  refundedCredits: number;
  versions: string[];
}

export type DetailMockScenario = 'success' | 'insufficient_credits' | 'partial_failure';

export type PromptModel = 'kimi-k3' | 'gemini-3.8-flash' | 'gpt-6-luna';
export interface PromptModelOption { id: PromptModel; name: string; available: boolean; reason: string | null }
export interface DetailImageTask {
  id: string; item_id: string; status: string; submission_state: string; created_at: string;
  result_data?: {url?: string; original_url?: string; thumbnail_url?: string; asset_id?: string; workspace_path?: string};
  error_message?: string; credits_used?: number; retry_of_task_id?: string;
}
export interface DetailGroup {
  execution_state?: 'waiting' | 'running';
  plan_id: string; kind: 'main_images' | 'detail_page'; status: string; stage: number; count: number;
  error?: {code?: string; message?: string; category?: string}; questions?: unknown[];
  acceptance_error?: {code?: string}; can_resume?: boolean; retry_may_have_provider_cost?: boolean;
  resume_request_id?: string | null;
  auto_recovery?: {enabled: boolean; status: 'active' | 'waiting' | 'blocked'; retry_cost: 'platform';
    attempts?: number; next_retry_at?: string | null; platform_attention?: boolean;
    items: Record<string, {status: 'waiting' | 'blocked'; message?: string | null; attempts: number; next_retry_at?: string | null; platform_attention?: boolean}>} | null;
  items: Array<{item_id: string; position: number; name: string; purpose: string; request_text: string; aspect_ratio: string}>;
  tasks: DetailImageTask[];
}

export interface DetailGenerationRun {
  run_id: string; created_at: string; requirement: string; groups: DetailGroup[];
}

export interface DetailTaskSummary {
  id: string; title: string; created_at: string; content_type: DetailContentType; status: string;
  display_status: 'draft'|'queued'|'selling_points'|'visual_direction'|'prompts'|'generating'|'recovering'|'completed'|'needs_attention';
  expected_count: number; completed_count: number; stage: number|null;
  thumbnail_url: string|null; recovery_waiting: boolean;
}
