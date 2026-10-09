import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import DetailPage from '../DetailPage';
import { useDetailPageStore } from '../../stores/useDetailPageStore';
import { getCurrentDetailProject, getDetailCapabilities } from '../../services/detailProject';
import { generateRequirementSuggestions } from '../../services/ecomRequirement';

vi.mock('../../services/detailProject', () => ({
  getCurrentDetailProject: vi.fn(), getDetailCapabilities: vi.fn(), getDetailProject: vi.fn(),startDetailProject:vi.fn(),archiveDetailProject:vi.fn(),
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
    vi.mocked(getDetailCapabilities).mockResolvedValue({enabled:true,prompt_models:[{id:'kimi-k3',name:'Kimi K3',available:true,reason:null}],image_models:[]});
    vi.mocked(generateRequirementSuggestions).mockReset();
  });

  it('显示固定双栏、默认数量和模型选择', async () => {
    renderPage();
    await waitFor(() => expect(useDetailPageStore.getState().isHydrating).toBe(false));
    expect(screen.getByRole('button',{name:'生成数量'})).toHaveTextContent('14张');
    expect(screen.getByRole('button',{name:'提示词模型'})).toHaveTextContent('Kimi');
    expect(screen.getByText('上传产品图并填写要求后，点击“开始生成”开始')).toBeInTheDocument();
    expect(screen.queryByText('确认图片规划')).not.toBeInTheDocument();
  });

  it('卸载只取消页面订阅并清理页面状态', () => {
    const {unmount}=renderPage();unmount();
    expect(useDetailPageStore.getState().groups).toEqual([]);
  });

  it('产品图就绪后打开 AI 帮写并将人工确认草稿回填要求', async () => {
    vi.mocked(getCurrentDetailProject).mockResolvedValue({
      id: 'project-1', version: 1, content_type: 'main_image', platform: 'auto', requirement: '',
      language: 'zh-CN', aspect_ratio: '1:1', quality: '1k', image_count: 1,
      images: [{ id: 'image-1', category: 'product', workspace_path: 'uploads/product.png', sort_order: 0, status: 'ready', original_url: 'product.png', thumbnail_url: null }],
    });
    vi.mocked(generateRequirementSuggestions).mockResolvedValue({
      success: true,
      data: {
        product_description: '突出已确认卖点',
        selling_points: [], creative_requirements: [],
        supplement_questions: [{ question: '本体尺寸是多少？', why: '补充规格', can_skip: true }],
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
    fireEvent.change(screen.getByLabelText('本体尺寸是多少？'), { target: { value: '20×14cm' } });
    fireEvent.change(screen.getByLabelText('其他补充或修改方向'), { target: { value: '背景改成深蓝色' } });
    fireEvent.click(screen.getByRole('button', { name: '采用内容' }));
    expect(useDetailPageStore.getState().form.requirement).toContain('突出已确认卖点');
    expect(useDetailPageStore.getState().form.requirement).toContain('20×14cm');
    expect(useDetailPageStore.getState().form.requirement).toContain('背景改成深蓝色');
    expect(generateRequirementSuggestions).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('没有草稿项目时禁用 AI 帮写', async () => {
    renderPage();
    await waitFor(() => expect(useDetailPageStore.getState().isHydrating).toBe(false));
    expect(screen.getByRole('button', { name: 'AI 帮写' })).toBeDisabled();
  });
});
