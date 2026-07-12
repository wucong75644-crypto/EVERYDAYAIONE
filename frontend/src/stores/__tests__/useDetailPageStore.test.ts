import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { useDetailPageStore } from '../useDetailPageStore';
import { getCurrentDetailProject } from '../../services/detailProject';
import { attachDetailImage } from '../../services/detailProject';
import { uploadImageFile } from '../../services/upload';

vi.mock('../../services/upload', () => ({ uploadImageFile: vi.fn(() => new Promise(() => undefined)) }));
vi.mock('../../services/detailProject', () => ({
  getCurrentDetailProject: vi.fn().mockResolvedValue(null),
  attachDetailImage: vi.fn(), removeDetailImage: vi.fn(), saveDetailSettings: vi.fn(),
}));

const createObjectURL = vi.fn(() => 'blob:preview');
const revokeObjectURL = vi.fn();

beforeAll(() => {
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL });
});

beforeEach(() => {
  useDetailPageStore.getState().reset();
  vi.clearAllMocks();
});

describe('useDetailPageStore', () => {
  it('使用已确认的默认设置', () => {
    const state = useDetailPageStore.getState();
    expect(state.step).toBe(1);
    expect(state.form).toMatchObject({ contentType: 'main_image', language: 'zh-CN', aspectRatio: '1:1', count: 1 });
  });

  it('页面卸载后忽略迟到的草稿恢复结果', async () => {
    let resolveDraft: (value: null) => void = () => undefined;
    vi.mocked(getCurrentDetailProject).mockImplementationOnce(() => new Promise((resolve) => { resolveDraft = resolve; }));
    const hydration = useDetailPageStore.getState().hydrateDraft();
    useDetailPageStore.getState().reset();
    resolveDraft(null);
    await hydration;
    expect(useDetailPageStore.getState().isHydrating).toBe(false);
  });

  it('上传关联后保留本地预览直到远程图片加载完成', async () => {
    vi.mocked(uploadImageFile).mockResolvedValueOnce({ url: 'https://cdn/result.png', workspace_path: '上传/result.png' });
    vi.mocked(attachDetailImage).mockResolvedValueOnce({
      id: 'project-1', version: 2, content_type: 'main_image', platform: 'auto', requirement: '',
      language: 'zh-CN', aspect_ratio: '1:1', quality: '1k', image_count: 1,
      images: [{ id: 'server-image', category: 'product', workspace_path: '上传/result.png', sort_order: 0, status: 'ready', original_url: 'https://cdn/result.png', thumbnail_url: null }],
    });
    let triggerLoad = () => undefined;
    const OriginalImage = globalThis.Image;
    vi.stubGlobal('Image', class {
      onload: (() => void) | null = null;
      set src(_value: string) { triggerLoad = () => this.onload?.(); }
    });
    await useDetailPageStore.getState().addImages('product', [new File(['x'], 'product.png', { type: 'image/png' })]);
    expect(useDetailPageStore.getState().images[0].previewUrl).toBe('blob:preview');
    triggerLoad();
    expect(useDetailPageStore.getState().images[0].previewUrl).toBe('https://cdn/result.png');
    vi.stubGlobal('Image', OriginalImage);
  });

  it('切换详情图时自动设置 3:4 比例', () => {
    useDetailPageStore.getState().updateForm({ contentType: 'detail_page' });
    expect(useDetailPageStore.getState().form.aspectRatio).toBe('3:4');
  });

  it('显式传比例时不覆盖用户选择', () => {
    useDetailPageStore.getState().updateForm({ contentType: 'detail_page', aspectRatio: '4:5' });
    expect(useDetailPageStore.getState().form.aspectRatio).toBe('4:5');
  });

  it('可以切换步骤和 Mock 场景', () => {
    useDetailPageStore.getState().setStep(3);
    useDetailPageStore.getState().setMockScenario('partial_failure');
    expect(useDetailPageStore.getState().step).toBe(3);
    expect(useDetailPageStore.getState().mockScenario).toBe('partial_failure');
  });

  it('reset 恢复默认状态并创建独立规划副本', () => {
    const firstPlan = useDetailPageStore.getState().plan;
    useDetailPageStore.getState().setStep(5);
    useDetailPageStore.getState().reset();
    const state = useDetailPageStore.getState();
    expect(state.step).toBe(1);
    expect(state.plan).toEqual(firstPlan);
    expect(state.plan).not.toBe(firstPlan);
  });

  it('产品图和参考图共享 9 张上限', () => {
    const files = Array.from({ length: 9 }, (_, index) => new File(['x'], `${index}.png`, { type: 'image/png' }));
    useDetailPageStore.getState().addImages('product', files.slice(0, 5));
    useDetailPageStore.getState().addImages('reference', files.slice(5));
    expect(useDetailPageStore.getState().images).toHaveLength(9);

    useDetailPageStore.getState().addImages('reference', [new File(['x'], 'extra.png', { type: 'image/png' })]);
    expect(useDetailPageStore.getState().images).toHaveLength(9);
    expect(useDetailPageStore.getState().formError).toContain('最多上传 9 张');
  });

  it('拒绝不支持的图片格式', () => {
    useDetailPageStore.getState().addImages('product', [new File(['x'], 'bad.gif', { type: 'image/gif' })]);
    expect(useDetailPageStore.getState().images).toEqual([]);
    expect(useDetailPageStore.getState().formError).toContain('JPG');
  });

  it('删除和重置时释放 ObjectURL', () => {
    const file = new File(['x'], 'product.png', { type: 'image/png' });
    useDetailPageStore.getState().addImages('product', [file]);
    const imageId = useDetailPageStore.getState().images[0].id;
    useDetailPageStore.getState().removeImage(imageId);
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:preview');

    useDetailPageStore.getState().addImages('reference', [file]);
    useDetailPageStore.getState().reset();
    expect(revokeObjectURL).toHaveBeenCalledTimes(2);
  });

  it('分析按阶段推进并生成指定数量的规划', () => {
    vi.useFakeTimers();
    useDetailPageStore.getState().addImages('product', [new File(['x'], 'product.png', { type: 'image/png' })]);
    useDetailPageStore.getState().updateForm({ count: 3 });
    useDetailPageStore.getState().startAnalysis();
    expect(useDetailPageStore.getState()).toMatchObject({ step: 2, isTransitioning: true });
    vi.advanceTimersByTime(2400);
    expect(useDetailPageStore.getState().step).toBe(3);
    expect(useDetailPageStore.getState().plan).toHaveLength(3);
    vi.useRealTimers();
  });

  it('取消分析后保留输入并停止推进', () => {
    vi.useFakeTimers();
    useDetailPageStore.getState().addImages('product', [new File(['x'], 'product.png', { type: 'image/png' })]);
    useDetailPageStore.getState().startAnalysis();
    useDetailPageStore.getState().cancelAnalysis();
    vi.advanceTimersByTime(3000);
    expect(useDetailPageStore.getState().step).toBe(1);
    expect(useDetailPageStore.getState().images).toHaveLength(1);
    vi.useRealTimers();
  });

  it('未上传产品图时拒绝分析', () => {
    useDetailPageStore.getState().startAnalysis();
    expect(useDetailPageStore.getState().step).toBe(1);
    expect(useDetailPageStore.getState().formError).toContain('产品图');
  });

  it('支持编辑、删除和重新规划且至少保留一张', () => {
    const firstId = useDetailPageStore.getState().plan[0].id;
    useDetailPageStore.getState().updatePlanItem(firstId, { title: '新标题' });
    expect(useDetailPageStore.getState().plan[0].title).toBe('新标题');
    useDetailPageStore.getState().removePlanItem(firstId);
    expect(useDetailPageStore.getState().plan).toHaveLength(2);
    useDetailPageStore.setState({ plan: [useDetailPageStore.getState().plan[0]] });
    useDetailPageStore.getState().removePlanItem(useDetailPageStore.getState().plan[0].id);
    expect(useDetailPageStore.getState().formError).toContain('至少保留');
    useDetailPageStore.getState().replan();
    expect(useDetailPageStore.getState().plan).toHaveLength(1);
  });

  it('逐张生成并在全部结束后进入完成页', () => {
    vi.useFakeTimers();
    useDetailPageStore.setState({ step: 3, plan: useDetailPageStore.getState().plan.slice(0, 2) });
    useDetailPageStore.getState().startGeneration();
    expect(useDetailPageStore.getState().generationItems[0].status).toBe('generating');
    vi.advanceTimersByTime(1400);
    expect(useDetailPageStore.getState().step).toBe(5);
    expect(useDetailPageStore.getState().generationItems.every((item) => item.status === 'completed')).toBe(true);
    vi.useRealTimers();
  });

  it('部分失败不阻塞其他图片并记录退款', () => {
    vi.useFakeTimers();
    useDetailPageStore.setState({ plan: useDetailPageStore.getState().plan.slice(0, 3), mockScenario: 'partial_failure' });
    useDetailPageStore.getState().startGeneration();
    vi.advanceTimersByTime(2100);
    const items = useDetailPageStore.getState().generationItems;
    expect(items.map((item) => item.status)).toEqual(['completed', 'failed', 'completed']);
    expect(items[1].refundedCredits).toBe(10);
    vi.useRealTimers();
  });

  it('积分不足时停留规划页', () => {
    useDetailPageStore.setState({ step: 3, mockScenario: 'insufficient_credits' });
    useDetailPageStore.getState().startGeneration();
    expect(useDetailPageStore.getState().step).toBe(3);
    expect(useDetailPageStore.getState().formError).toContain('积分不足');
  });

  it('重试追加版本，再次制作保留输入，返回方案清空结果', () => {
    const item = { ...useDetailPageStore.getState().plan[0], status: 'failed' as const, previewUrl: null, error: '失败', refundedCredits: 10, versions: ['old'] };
    useDetailPageStore.setState({ step: 5, generationItems: [item] });
    useDetailPageStore.getState().retryGeneration(item.id);
    expect(useDetailPageStore.getState().generationItems[0].versions).toHaveLength(2);
    useDetailPageStore.getState().restart();
    expect(useDetailPageStore.getState()).toMatchObject({ step: 1, generationItems: [] });
    useDetailPageStore.setState({ step: 5, generationItems: [item] });
    useDetailPageStore.getState().backToPlan();
    expect(useDetailPageStore.getState()).toMatchObject({ step: 3, generationItems: [] });
  });
});
