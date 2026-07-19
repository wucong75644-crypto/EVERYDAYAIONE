import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import PdfPreviewControls from '../PdfPreviewControls';
import PreviewHost from '../../PreviewHost';

vi.mock('../PdfPreview', () => ({
  default: () => <div>PDF renderer ready</div>,
}));

vi.mock('../PptxPreview', () => ({
  default: () => <div>Office renderer ready</div>,
}));

const commonProps = {
  onClose: vi.fn(),
  onNavigate: vi.fn(),
};

describe('lazy document preview adapters', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads the PDF renderer only after the PDF adapter is rendered', async () => {
    render(
      <PreviewHost
        state={{
          kind: 'open',
          items: [{ filename: 'report.pdf', url: '/report.pdf' }],
          index: 0,
        }}
        onClose={commonProps.onClose}
        onIndexChange={commonProps.onNavigate}
      />,
    );

    expect(screen.getByText('正在加载文件预览器...')).toBeTruthy();
    expect(await screen.findByText('PDF renderer ready')).toBeTruthy();
  });

  it('loads the Office renderer only after the PPT adapter is rendered', async () => {
    render(
      <PreviewHost
        state={{
          kind: 'open',
          items: [{ filename: 'slides.pptx', workspacePath: 'slides.pptx' }],
          index: 0,
        }}
        onClose={commonProps.onClose}
        onIndexChange={commonProps.onNavigate}
      />,
    );

    expect(screen.getByText('正在加载文件预览器...')).toBeTruthy();
    expect(await screen.findByText('Office renderer ready')).toBeTruthy();
  });
});

describe('PDF preview controls', () => {
  it('disables navigation and zoom actions at their boundaries', () => {
    render(
      <PdfPreviewControls
        pageNumber={1}
        numPages={1}
        scale={0.5}
        minScale={0.5}
        maxScale={0.5}
        onPrevious={vi.fn()}
        onNext={vi.fn()}
        onZoomOut={vi.fn()}
        onZoomIn={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '缩小' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '放大' })).toBeDisabled();
  });

  it('forwards enabled navigation and zoom actions', () => {
    const onPrevious = vi.fn();
    const onNext = vi.fn();
    const onZoomOut = vi.fn();
    const onZoomIn = vi.fn();
    render(
      <PdfPreviewControls
        pageNumber={2}
        numPages={3}
        scale={1}
        minScale={0.5}
        maxScale={3}
        onPrevious={onPrevious}
        onNext={onNext}
        onZoomOut={onZoomOut}
        onZoomIn={onZoomIn}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '上一页' }));
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    fireEvent.click(screen.getByRole('button', { name: '缩小' }));
    fireEvent.click(screen.getByRole('button', { name: '放大' }));

    expect(onPrevious).toHaveBeenCalledOnce();
    expect(onNext).toHaveBeenCalledOnce();
    expect(onZoomOut).toHaveBeenCalledOnce();
    expect(onZoomIn).toHaveBeenCalledOnce();
  });
});
