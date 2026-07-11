import type { DetailGenerationForm, DetailPlanItem } from '../types/detailPage';

export const DETAIL_STEP_LABELS = ['输入', '分析中', '确认规划', '生成中', '完成'] as const;

export const DETAIL_ANALYSIS_STAGES = ['识别产品主体', '分析视觉特征', '提取核心卖点', '规划图片组合'] as const;

export const MOCK_RESULT_COLORS = ['6d5dfc', '0f9d8a', 'e07832', '3d72d9', 'b94f78', '64748b'] as const;

export function createMockResultUrl(index: number, version = 1) {
  const color = MOCK_RESULT_COLORS[index % MOCK_RESULT_COLORS.length];
  const label = `AI Image ${index + 1} V${version}`;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="800" height="800"><rect width="800" height="800" fill="#${color}"/><text x="400" y="410" fill="white" font-family="sans-serif" font-size="42" text-anchor="middle">${label}</text></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

export const DEFAULT_DETAIL_FORM: DetailGenerationForm = {
  contentType: 'main_image',
  platform: 'auto',
  requirement: '',
  language: 'zh-CN',
  aspectRatio: '1:1',
  quality: '1k',
  count: 1,
};

export const MOCK_DETAIL_PLAN: DetailPlanItem[] = [
  {
    id: 'mock-plan-1',
    role: '钩子主图',
    purpose: '快速传达产品核心卖点',
    composition: '产品居中，简洁背景，突出主体',
    title: '核心卖点标题',
    subtitle: '产品优势说明',
    prompt: 'Create a clean e-commerce hero image with the product centered.',
    aspectRatio: '1:1',
    hasText: true,
  },
  {
    id: 'mock-plan-2',
    role: '卖点展示',
    purpose: '解释产品的关键优势',
    composition: '产品特写搭配简洁信息标签',
    title: '看得见的产品优势',
    subtitle: '核心功能清晰呈现',
    prompt: 'Create an e-commerce feature image with a close-up product view and clean labels.',
    aspectRatio: '1:1',
    hasText: true,
  },
  {
    id: 'mock-plan-3',
    role: '场景展示',
    purpose: '帮助用户理解实际使用体验',
    composition: '自然生活场景，产品作为视觉焦点',
    title: '融入日常使用场景',
    subtitle: '直观呈现使用方式',
    prompt: 'Create a natural lifestyle scene with the product as the visual focus.',
    aspectRatio: '1:1',
    hasText: true,
  },
];
