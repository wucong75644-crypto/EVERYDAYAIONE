import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import DetailPage from '../DetailPage';
import { useDetailPageStore } from '../../stores/useDetailPageStore';

vi.mock('../../services/detailProject', () => ({
  getCurrentDetailProject: vi.fn(() => new Promise(() => undefined)),
  attachDetailImage: vi.fn(), removeDetailImage: vi.fn(), saveDetailSettings: vi.fn(),
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
  });

  it('显示标题、五步进度和双栏骨架', () => {
    renderPage();
    expect(screen.queryByText('AI 帮写需求，一键生成详情图组')).not.toBeInTheDocument();
    expect(screen.queryByText('上传产品图，AI 智能分析并规划多角度、多场景的电商图片')).not.toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(5);
    expect(screen.getByText('产品图片')).toBeInTheDocument();
    expect(screen.getByText('目标平台')).toBeInTheDocument();
    expect(screen.getByText('上传产品图并填写要求后，点击“分析产品”开始')).toBeInTheDocument();
    expect(screen.queryByText('AI 记忆')).not.toBeInTheDocument();
  });

  it('Store 步骤变化后显示对应状态文案', () => {
    useDetailPageStore.getState().setStep(3);
    renderPage();
    expect(screen.getByText('确认图片规划')).toBeInTheDocument();
    expect(screen.getByText(/可在生成前调整文案和提示词/)).toBeInTheDocument();
  });

  it('页面卸载时清理专用状态', () => {
    useDetailPageStore.getState().setStep(4);
    const { unmount } = renderPage();
    unmount();
    expect(useDetailPageStore.getState().step).toBe(1);
  });
});
