/** 提示词画廊数据类型 */

export interface PromptCategory {
  id: string;
  name: string;
  name_en: string;
  icon: string;
  count: number;
}

export interface PromptItem {
  id: string;
  title: string;
  title_en: string;
  category: string;
  description: string;
  tags: string[];
  prompt: string;
  preview_url: string;
  aspect_ratio: string;
  source: string;
  source_url: string;
  source_author: string;
}

export interface PromptGalleryData {
  version: string;
  updated_at: string;
  source_repo: string;
  license: string;
  categories: PromptCategory[];
  prompts: PromptItem[];
}
