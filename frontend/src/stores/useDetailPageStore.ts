import { create } from 'zustand';
import { createMockResultUrl, DEFAULT_DETAIL_FORM, MOCK_DETAIL_PLAN } from '../mocks/detailPageMocks';
import type {
  DetailGenerationForm,
  DetailGenerationItem,
  DetailLocalImage,
  DetailMockScenario,
  DetailPageStep,
  DetailPlanItem,
} from '../types/detailPage';

interface DetailPageState {
  step: DetailPageStep;
  images: DetailLocalImage[];
  form: DetailGenerationForm;
  analysisStage: number;
  plan: DetailPlanItem[];
  generationItems: DetailGenerationItem[];
  isTransitioning: boolean;
  formError: string | null;
  mockScenario: DetailMockScenario;
  setStep: (step: DetailPageStep) => void;
  addImages: (category: DetailLocalImage['category'], files: File[]) => void;
  removeImage: (id: string) => void;
  updateForm: (patch: Partial<DetailGenerationForm>) => void;
  startAnalysis: () => void;
  cancelAnalysis: () => void;
  updatePlanItem: (id: string, patch: Partial<DetailPlanItem>) => void;
  removePlanItem: (id: string) => void;
  replan: () => void;
  startGeneration: () => void;
  retryGeneration: (id: string) => void;
  backToPlan: () => void;
  restart: () => void;
  setMockScenario: (scenario: DetailMockScenario) => void;
  reset: () => void;
}

const initialState = {
  step: 1 as DetailPageStep,
  images: [] as DetailLocalImage[],
  form: { ...DEFAULT_DETAIL_FORM },
  analysisStage: 0,
  plan: MOCK_DETAIL_PLAN.map((item) => ({ ...item })),
  generationItems: [] as DetailGenerationItem[],
  isTransitioning: false,
  formError: null as string | null,
  mockScenario: 'success' as DetailMockScenario,
};

const ALLOWED_IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp']);
const MAX_IMAGES = 9;
let analysisTimer: ReturnType<typeof setInterval> | null = null;
let generationTimer: ReturnType<typeof setInterval> | null = null;

function clearAnalysisTimer() {
  if (analysisTimer) clearInterval(analysisTimer);
  analysisTimer = null;
}

function clearGenerationTimer() {
  if (generationTimer) clearInterval(generationTimer);
  generationTimer = null;
}

function createPlan(count: number) {
  return Array.from({ length: count }, (_, index) => ({
    ...MOCK_DETAIL_PLAN[index % MOCK_DETAIL_PLAN.length],
    id: `mock-plan-${Date.now()}-${index}`,
  }));
}

function releasePreview(image: DetailLocalImage) {
  if (image.previewUrl.startsWith('blob:')) URL.revokeObjectURL(image.previewUrl);
}

export const useDetailPageStore = create<DetailPageState>((set, get) => ({
  ...initialState,
  setStep: (step) => set({ step }),
  addImages: (category, files) => {
    const currentImages = get().images;
    if (currentImages.length + files.length > MAX_IMAGES) {
      set({ formError: `产品图和参考图合计最多上传 ${MAX_IMAGES} 张` });
      return;
    }
    const invalidFile = files.find((file) => !ALLOWED_IMAGE_TYPES.has(file.type));
    if (invalidFile) {
      set({ formError: '仅支持 JPG、PNG、WebP 格式的图片' });
      return;
    }
    const newImages = files.map((file, index): DetailLocalImage => ({
      id: `${Date.now()}-${index}-${file.name}`,
      category,
      file,
      previewUrl: URL.createObjectURL(file),
      error: null,
    }));
    set((state) => ({ images: [...state.images, ...newImages], formError: null }));
  },
  removeImage: (id) => {
    const image = get().images.find((item) => item.id === id);
    if (image) releasePreview(image);
    set((state) => ({ images: state.images.filter((item) => item.id !== id), formError: null }));
  },
  updateForm: (patch) => {
    set((state) => {
      const nextPatch = { ...patch };
      if (patch.contentType && !patch.aspectRatio) {
        nextPatch.aspectRatio = patch.contentType === 'detail_page' ? '3:4' : '1:1';
      }
      return { form: { ...state.form, ...nextPatch } };
    });
  },
  startAnalysis: () => {
    if (get().isTransitioning) return;
    if (!get().images.some((image) => image.category === 'product')) {
      set({ formError: '请至少上传一张产品图' });
      return;
    }
    clearAnalysisTimer();
    set({ step: 2, analysisStage: 0, isTransitioning: true, formError: null });
    analysisTimer = setInterval(() => {
      const nextStage = get().analysisStage + 1;
      if (nextStage >= 4) {
        clearAnalysisTimer();
        set({ step: 3, analysisStage: 3, plan: createPlan(get().form.count), isTransitioning: false });
        return;
      }
      set({ analysisStage: nextStage });
    }, 600);
  },
  cancelAnalysis: () => {
    clearAnalysisTimer();
    set({ step: 1, analysisStage: 0, isTransitioning: false });
  },
  updatePlanItem: (id, patch) => set((state) => ({
    plan: state.plan.map((item) => item.id === id ? { ...item, ...patch } : item),
  })),
  removePlanItem: (id) => set((state) => state.plan.length <= 1
    ? { formError: '规划至少保留 1 张图片' }
    : { plan: state.plan.filter((item) => item.id !== id), formError: null }),
  replan: () => set((state) => ({ plan: createPlan(state.form.count), formError: null })),
  startGeneration: () => {
    if (get().isTransitioning) return;
    if (get().mockScenario === 'insufficient_credits') {
      set({ formError: '积分不足，请减少生成数量后重试' });
      return;
    }
    const items = get().plan.map((item) => ({ ...item, status: 'waiting' as const, previewUrl: null, error: null, refundedCredits: 0, versions: [] }));
    clearGenerationTimer();
    set({ step: 4, generationItems: items, isTransitioning: true, formError: null });
    let currentIndex = 0;
    set((state) => ({ generationItems: state.generationItems.map((item, index) => index === 0 ? { ...item, status: 'generating' } : item) }));
    generationTimer = setInterval(() => {
      const shouldFail = get().mockScenario === 'partial_failure' && currentIndex === 1;
      set((state) => ({ generationItems: state.generationItems.map((item, index) => {
        if (index === currentIndex) return shouldFail
          ? { ...item, status: 'failed', error: 'Mock 生成服务暂时不可用', refundedCredits: 10 }
          : { ...item, status: 'completed', previewUrl: createMockResultUrl(index), versions: [createMockResultUrl(index)] };
        if (index === currentIndex + 1) return { ...item, status: 'generating' };
        return item;
      }) }));
      currentIndex += 1;
      if (currentIndex >= get().generationItems.length) {
        clearGenerationTimer();
        set({ step: 5, isTransitioning: false });
      }
    }, 700);
  },
  retryGeneration: (id) => set((state) => ({ generationItems: state.generationItems.map((item, index) => {
    if (item.id !== id) return item;
    const nextUrl = createMockResultUrl(index, item.versions.length + 1);
    return { ...item, status: 'completed', previewUrl: nextUrl, error: null, refundedCredits: 0, versions: [...item.versions, nextUrl] };
  }) })),
  backToPlan: () => {
    clearGenerationTimer();
    set({ step: 3, generationItems: [], isTransitioning: false });
  },
  restart: () => {
    clearGenerationTimer();
    set({ step: 1, analysisStage: 0, generationItems: [], isTransitioning: false, formError: null });
  },
  setMockScenario: (mockScenario) => set({ mockScenario }),
  reset: () => {
    clearAnalysisTimer();
    clearGenerationTimer();
    get().images.forEach(releasePreview);
    set({
      ...initialState,
      images: [],
      form: { ...DEFAULT_DETAIL_FORM },
      plan: MOCK_DETAIL_PLAN.map((item) => ({ ...item })),
    });
  },
}));
