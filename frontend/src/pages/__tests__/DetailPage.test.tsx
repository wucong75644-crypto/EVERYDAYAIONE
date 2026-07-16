import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import DetailPage from '../DetailPage';
import { useDetailPageStore } from '../../stores/useDetailPageStore';
import { getCurrentDetailProject } from '../../services/detailProject';
import { generateRequirementSuggestions } from '../../services/ecomRequirement';

vi.mock('../../services/detailProject', () => ({
  getCurrentDetailProject: vi.fn(),
  attachDetailImage: vi.fn(), removeDetailImage: vi.fn(), saveDetailSettings: vi.fn(),
}));

vi.mock('../../services/ecomRequirement', () => ({
  generateRequirementSuggestions: vi.fn(),
}));

vi.mock('../../stores/useAuthStore', () => ({
  useAuthStore: (selector: (state: { user: { nickname: string; credits: number } }) => unknown) =>
    selector({ user: { nickname: '测试用户', credits: 100 } }),
}));

vi.mock('../../components/motion/PageTransition', () => ({
  PageTransition: ({ children, className }: { children: React.ReactNode; className?: string }) => (
    <div className={className}>{children}</div>
  ),
}));

function renderPage() {
  return render(<MemoryRouter><DetailPage /></MemoryRouter>);
}

describe('DetailPage 页面骨架', () => {
  beforeEach(() => {
    useDetailPageStore.getState().reset();
    vi.mocked(getCurrentDetailProject).mockResolvedValue(null);
    vi.mocked(generateRequirementSuggestions).mockReset();
  });

  it('显示标题、五步进度和双栏骨架', async () => {
    renderPage();
    await waitFor(() => expect(useDetailPageStore.getState().isHydrating).toBe(false));
    expect(screen.queryByText('AI 帮写需求，一键生成详情图组')).not.toBeInTheDocument();
    expect(screen.queryByText('上传产品图，AI 智能分析并规划多角度、多场景的电商图片')).not.toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(5);
    expect(screen.getByText('产品图片')).toBeInTheDocument();
    expect(screen.getByText('目标平台')).toBeInTheDocument();
    expect(screen.getByText('上传产品图并填写要求后，点击“分析产品”开始')).toBeInTheDocument();
    expect(screen.queryByText('AI 记忆')).not.toBeInTheDocument();
  });

  it('Store 步骤变化后显示对应状态文案', async () => {
    useDetailPageStore.getState().setStep(3);
    renderPage();
    await waitFor(() => expect(useDetailPageStore.getState().isHydrating).toBe(false));
    expect(screen.getByText('确认图片规划')).toBeInTheDocument();
    expect(screen.getByText(/可在生成前调整文案和提示词/)).toBeInTheDocument();
  });

  it('页面卸载时清理专用状态', () => {
    useDetailPageStore.getState().setStep(4);
    const { unmount } = renderPage();
    unmount();
    expect(useDetailPageStore.getState().step).toBe(1);
  });

  it('产品图就绪后打开 AI 帮写并将选中方案回填要求', async () => {
    vi.mocked(getCurrentDetailProject).mockResolvedValue({
      id: 'project-1', version: 1, content_type: 'main_image', platform: 'auto', requirement: '',
      language: 'zh-CN', aspect_ratio: '1:1', quality: '1k', image_count: 1,
      images: [{ id: 'image-1', category: 'product', workspace_path: 'uploads/product.png', sort_order: 0, status: 'ready', original_url: 'product.png', thumbnail_url: null }],
    });
    vi.mocked(generateRequirementSuggestions).mockResolvedValue({
      success: true,
      data: {
        product_facts: { product_name: '测试产品', confirmed_attributes: ['蓝色'], unclear_items: [] },
        reference_analyses: [], conflicts: [],
        suggestions: [
          { id: 'selling_point', name: '卖点方案', style_name: '清晰', brief_markdown: '突出已确认卖点' },
          { id: 'scene', name: '场景方案', style_name: '自然', brief_markdown: '自然场景展示' },
          { id: 'creative', name: '创意方案', style_name: '创意', brief_markdown: '创意视觉展示' },
        ],
      },
      error: null,
      meta: { model: 'test', fallback_used: false, latency_ms: 10, project_version: 1 },
    });

    renderPage();
    const assistButton = await screen.findByRole('button', { name: 'AI 帮写' });
    await waitFor(() => expect(assistButton).toBeEnabled());
    fireEvent.click(assistButton);

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    await screen.findByDisplayValue('突出已确认卖点');
    fireEvent.click(screen.getByRole('button', { name: '确认选择' }));
    expect(useDetailPageStore.getState().form.requirement).toBe('突出已确认卖点');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('没有草稿项目时禁用 AI 帮写', async () => {
    renderPage();
    await waitFor(() => expect(useDetailPageStore.getState().isHydrating).toBe(false));
    expect(screen.getByRole('button', { name: 'AI 帮写' })).toBeDisabled();
  });
});
