export type DetailPageStep = 1 | 2 | 3 | 4 | 5;
export type DetailContentType = 'main_image' | 'detail_page';
export type DetailImageCategory = 'product' | 'reference';
export type DetailItemStatus = 'waiting' | 'generating' | 'completed' | 'failed';
export type DetailImageStatus = 'local' | 'uploading' | 'attaching' | 'ready' | 'failed' | 'missing';

export interface DetailLocalImage {
  id: string;
  category: DetailImageCategory;
  file?: File;
  previewUrl: string;
  error: string | null;
  workspacePath?: string;
  status: DetailImageStatus;
  sortOrder?: number;
  name: string;
}

export interface DetailProjectDraft {
  id: string;
  version: number;
  content_type: DetailContentType;
  platform: DetailGenerationForm['platform'];
  requirement: string;
  language: DetailGenerationForm['language'];
  aspect_ratio: string;
  quality: DetailGenerationForm['quality'];
  image_count: number;
  images: Array<{
    id: string; category: DetailImageCategory; workspace_path: string; sort_order: number;
    status: 'ready' | 'missing'; original_url: string | null; thumbnail_url: string | null;
  }>;
}

export interface DetailGenerationForm {
  contentType: DetailContentType;
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
