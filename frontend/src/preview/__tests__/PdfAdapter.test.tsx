import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ReactNode, ComponentType } from 'react';
import type { PreviewCommonProps } from '../types';
import { pdfAdapter } from '../adapters/PdfAdapter';
vi.mock('react-pdf', () => ({
  pdfjs: { GlobalWorkerOptions: {} },
  Document: ({onLoadSuccess,children}: {onLoadSuccess:(v:{numPages:number})=>void;children:ReactNode}) => <div><button onClick={() => onLoadSuccess({numPages:1})}>完成 PDF 载入</button>{children}</div>,
  Page: () => <span>实际 PDF 页面</span>,
}));
describe('PDF loading lifecycle', () => {
  it('mounts the resource while showing its loading UI so it can finish loading', () => {
    const Component = pdfAdapter.Component as ComponentType<PreviewCommonProps>;
    const item = { filename:'private.pdf',url:'blob:private-pdf' };
    const view = render(<Component item={item} siblings={[item]} index={0} onClose={vi.fn()} onNavigate={vi.fn()} />);
    expect(screen.getByText('加载 PDF 中...')).toBeVisible();
    // It is hidden while loading, but must exist to run the real PDF loader.
    fireEvent.click(screen.getByText('完成 PDF 载入'));
    expect(screen.getByText('实际 PDF 页面')).toBeVisible();
    expect(screen.queryByText('加载 PDF 中...')).not.toBeInTheDocument();
    expect(screen.getByText('1 / 1')).toBeVisible();
    const next = { filename:'next.pdf',url:'blob:next-private-pdf' };
    view.rerender(<Component item={next} siblings={[next]} index={0} onClose={vi.fn()} onNavigate={vi.fn()} />);
    expect(screen.getByText('加载 PDF 中...')).toBeVisible();
  });
});
