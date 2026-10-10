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

  it('插入时将编辑产品、增补卖点、风格和客户回答一起回填输入框，不再次调用模型', async () => {
    vi.mocked(getCurrentDetailProject).mockResolvedValue({
      id: 'project-1', version: 1, content_type: 'main_image', platform: 'auto', requirement: '需要清楚展示商品',
      language: 'zh-CN', aspect_ratio: '1:1', quality: '1k', image_count: 1,
      images: [{ id: 'image-1', category: 'product', workspace_path: 'uploads/product.png', sort_order: 0, status: 'ready', original_url: 'product.png', thumbnail_url: null }],
    });
    vi.mocked(generateRequirementSuggestions).mockResolvedValue({
      success: true,
      data: {
        product_description: '突出已确认卖点',
        selling_points: [{feature:'书本式结构',benefit:'便于整理',benefit_basis:'inferred'}],
        creative_requirements: [{topic:'背景',text:'米白背景',basis:'suggested'}],
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
    fireEvent.change(screen.getByLabelText('产品细节与规格'), { target: { value: 'A6活页本，可替换内芯' } });
    fireEvent.change(screen.getByLabelText('卖点1价值'), { target: { value: '可按需整理内容' } });
    fireEvent.click(screen.getByRole('button', { name: '补充卖点' }));
    fireEvent.change(screen.getByLabelText('卖点2特点'), { target: { value: '空白米白内页' } });
    fireEvent.change(screen.getByLabelText('卖点2价值'), { target: { value: '可书写和拼贴' } });
    fireEvent.change(screen.getByLabelText('背景 · AI 建议'), { target: { value: '简洁背景，保留商品颜色' } });
    fireEvent.change(screen.getByLabelText('本体尺寸是多少？'), { target: { value: '20×14cm' } });
    fireEvent.change(screen.getByLabelText('其他补充或修改方向'), { target: { value: '背景改成深蓝色' } });
    fireEvent.click(screen.getByRole('button', { name: '插入到输入框' }));
    const inserted = useDetailPageStore.getState().form.requirement;
    for (const text of ['需要清楚展示商品','A6活页本，可替换内芯','可按需整理内容','空白米白内页','可书写和拼贴','简洁背景，保留商品颜色','20×14cm','背景改成深蓝色']) {
      expect(inserted).toContain(text);
    }
    expect(screen.getByRole('textbox', { name: '主图要求' })).toHaveValue(inserted);
    expect(generateRequirementSuggestions).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('没有草稿项目时禁用 AI 帮写', async () => {
    renderPage();
    await waitFor(() => expect(useDetailPageStore.getState().isHydrating).toBe(false));
    expect(screen.getByRole('button', { name: 'AI 帮写' })).toBeDisabled();
  });
});
