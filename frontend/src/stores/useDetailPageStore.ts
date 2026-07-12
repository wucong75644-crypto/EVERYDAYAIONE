import { create } from 'zustand';
import { createMockResultUrl, DEFAULT_DETAIL_FORM, MOCK_DETAIL_PLAN } from '../mocks/detailPageMocks';
import { attachDetailImage, getCurrentDetailProject, removeDetailImage, saveDetailSettings } from '../services/detailProject';
import { uploadImageFile } from '../services/upload';
import { toApiRequestError } from '../services/api';
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
  projectId: string | null;
  projectVersion: number | null;
  isHydrating: boolean;
  mockScenario: DetailMockScenario;
  setStep: (step: DetailPageStep) => void;
  hydrateDraft: () => Promise<void>;
  attachWorkspaceImages: (category: DetailLocalImage['category'], paths: string[]) => Promise<void>;
  addImages: (category: DetailLocalImage['category'], files: File[]) => Promise<void>;
  removeImage: (id: string) => Promise<void>;
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
  projectId: null as string | null,
  projectVersion: null as number | null,
  isHydrating: false,
};

const ALLOWED_IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp']);
const MAX_IMAGES = 9;
let analysisTimer: ReturnType<typeof setInterval> | null = null;
let generationTimer: ReturnType<typeof setInterval> | null = null;
let settingsTimer: ReturnType<typeof setTimeout> | null = null;
let lifecycleVersion = 0;

function clearAnalysisTimer() {
  if (analysisTimer) clearInterval(analysisTimer);
  analysisTimer = null;
}

function clearGenerationTimer() {
  if (generationTimer) clearInterval(generationTimer);
  generationTimer = null;
}

function clearSettingsTimer() {
  if (settingsTimer) clearTimeout(settingsTimer);
  settingsTimer = null;
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

function applyDraft(project: import('../types/detailPage').DetailProjectDraft | null) {
  if (!project) return { projectId: null, projectVersion: null, images: [] as DetailLocalImage[] };
  return {
    projectId: project.id,
    projectVersion: project.version,
    form: {
      contentType: project.content_type, platform: project.platform, requirement: project.requirement,
      language: project.language, aspectRatio: project.aspect_ratio, quality: project.quality, count: project.image_count,
    },
    images: project.images.map((image) => ({
      id: image.id, category: image.category, workspacePath: image.workspace_path,
      previewUrl: image.thumbnail_url || image.original_url || '', error: null,
      status: image.status, sortOrder: image.sort_order, name: image.workspace_path.split('/').pop() || '图片',
    })),
  };
}

export const useDetailPageStore = create<DetailPageState>((set, get) => ({
  ...initialState,
  setStep: (step) => set({ step }),
  hydrateDraft: async () => {
    const requestVersion = lifecycleVersion;
    set({ isHydrating: true, formError: null });
    try {
      const project = await getCurrentDetailProject();
      if (requestVersion !== lifecycleVersion) return;
      set({ ...applyDraft(project), isHydrating: false });
    } catch (error) {
      if (requestVersion !== lifecycleVersion) return;
      set({ isHydrating: false, formError: toApiRequestError(error).message });
    }
  },
  attachWorkspaceImages: async (category, paths) => {
    if (get().images.length + paths.length > MAX_IMAGES) {
      set({ formError: `产品图和参考图合计最多上传 ${MAX_IMAGES} 张` });
      return;
    }
    for (const path of paths) {
      try {
        const project = await attachDetailImage(path, category);
        set({ ...applyDraft(project), formError: null });
      } catch (error) {
        set({ formError: toApiRequestError(error).message });
        break;
      }
    }
  },
  addImages: async (category, files) => {
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
      status: 'local',
      name: file.name,
    }));
    set((state) => ({ images: [...state.images, ...newImages], formError: null }));
    for (const image of newImages) {
      try {
        set((state) => ({ images: state.images.map((item) => item.id === image.id ? { ...item, status: 'uploading' } : item) }));
        const uploaded = await uploadImageFile(image.file!);
        if (!uploaded.workspace_path) throw new Error('上传结果缺少工作区路径');
        set((state) => ({ images: state.images.map((item) => item.id === image.id ? { ...item, status: 'attaching', workspacePath: uploaded.workspace_path } : item) }));
        const project = await attachDetailImage(uploaded.workspace_path, category);
        const remotePreview = uploaded.thumbnail_url || uploaded.preview_url || uploaded.url;
        const requestVersion = lifecycleVersion;
        set((state) => {
          const draft = applyDraft(project);
          const images = draft.images.map((item) => item.workspacePath === uploaded.workspace_path
            ? { ...item, previewUrl: image.previewUrl }
            : item);
          const pending = state.images.filter((item) => item.id !== image.id && ['local', 'uploading', 'attaching', 'failed'].includes(item.status));
          return { ...draft, images: [...images, ...pending], formError: null };
        });
        if (remotePreview) {
          const remoteImage = new Image();
          remoteImage.onload = () => {
            if (requestVersion !== lifecycleVersion) return;
            set((state) => ({ images: state.images.map((item) => item.workspacePath === uploaded.workspace_path
              ? { ...item, previewUrl: remotePreview }
              : item) }));
            setTimeout(() => URL.revokeObjectURL(image.previewUrl), 30000);
          };
          remoteImage.src = remotePreview;
        }
      } catch (error) {
        set((state) => ({ images: state.images.map((item) => item.id === image.id ? { ...item, status: 'failed', error: toApiRequestError(error).message } : item), formError: toApiRequestError(error).message }));
      }
    }
  },
  removeImage: async (id) => {
    const image = get().images.find((item) => item.id === id);
    if (!image) return;
    if (!get().projectId || get().projectVersion === null || image.status !== 'ready') {
      releasePreview(image);
      set((state) => ({ images: state.images.filter((item) => item.id !== id), formError: null }));
      return;
    }
    try {
      const project = await removeDetailImage(get().projectId!, id, get().projectVersion!);
      set({ ...applyDraft(project), formError: null });
    } catch (error) {
      set({ formError: toApiRequestError(error).message });
    }
  },
  updateForm: (patch) => {
    set((state) => {
      const nextPatch = { ...patch };
      if (patch.contentType && !patch.aspectRatio) {
        nextPatch.aspectRatio = patch.contentType === 'detail_page' ? '3:4' : '1:1';
      }
      return { form: { ...state.form, ...nextPatch } };
    });
    clearSettingsTimer();
    settingsTimer = setTimeout(() => {
      const { projectId, projectVersion, form } = get();
      if (!projectId || projectVersion === null) return;
      void saveDetailSettings(projectId, projectVersion, form).then((project) => {
        if (project) set({ ...applyDraft(project), formError: null });
      }).catch((error) => {
        const apiError = toApiRequestError(error);
        set({ formError: apiError.message });
        if (apiError.code === 'DETAIL_PROJECT_VERSION_CONFLICT') void get().hydrateDraft();
      });
    }, 500);
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
    lifecycleVersion += 1;
    clearAnalysisTimer();
    clearGenerationTimer();
    clearSettingsTimer();
    get().images.forEach(releasePreview);
    set({
      ...initialState,
      images: [],
      projectId: null,
      projectVersion: null,
      isHydrating: false,
      form: { ...DEFAULT_DETAIL_FORM },
      plan: MOCK_DETAIL_PLAN.map((item) => ({ ...item })),
    });
  },
}));
