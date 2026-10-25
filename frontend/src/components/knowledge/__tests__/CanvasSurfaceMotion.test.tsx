import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useRef, useState } from 'react';
import { Dialog } from '../../primitives/Dialog';
import ImagePreviewModal from '../../chat/media/ImagePreviewModal';
import VideoPreviewModal from '../../chat/media/VideoPreviewModal';
import { CanvasSurfaceMotion } from '../CanvasSurfaceMotion';
import { surfaceFrames, useCanvasSurfaceMotion } from '../useCanvasSurfaceMotion';
const originalAnimate = HTMLElement.prototype.animate;
let animate: ReturnType<typeof vi.fn>, media: { matches: boolean; addEventListener: ReturnType<typeof vi.fn>; removeEventListener: ReturnType<typeof vi.fn> }, listeners: Set<() => void>;
function Surface() { const ref = useRef<HTMLDivElement>(null), attach = useCanvasSurfaceMotion(ref, 'panel'); return <div ref={attach} role="complementary" id="real-panel"><input aria-label="内容" defaultValue="原值"/><button>关闭入口</button></div>; }
function Fixture({ identity = 'u:org', reduced = false }) { const [open, setOpen] = useState(true); return <CanvasSurfaceMotion identity={identity} reduced={reduced}><button onClick={() => setOpen(v => !v)}>切换</button>{open && <Surface/>}</CanvasSurfaceMotion>; }
beforeEach(() => {
  vi.useFakeTimers(); listeners = new Set(); media = { matches: false, addEventListener: vi.fn((_: string, fn: () => void) => listeners.add(fn)), removeEventListener: vi.fn((_: string, fn: () => void) => listeners.delete(fn)) };
  vi.stubGlobal('matchMedia', vi.fn(() => media));
  animate = vi.fn(() => ({ cancel: vi.fn(), onfinish: null })); Object.defineProperty(HTMLElement.prototype, 'animate', { configurable: true, value: animate });
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ x: 100, y: 60, left: 100, top: 60, width: 400, height: 500, right: 500, bottom: 560, toJSON: () => ({}) });
});
afterEach(() => { Object.defineProperty(HTMLElement.prototype, 'animate', { configurable: true, value: originalAnimate }); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
describe('canvas surface motion', () => {
  it('releases a canvas dialog and its focus scope immediately while only inert content fades', async () => {
    function DialogFixture() { const [open, setOpen] = useState(true); return <CanvasSurfaceMotion identity="u" reduced={false}><button>画布目标</button>{open && <Dialog open motionPreset="canvas" title="编辑" onOpenChange={setOpen}><button onClick={() => setOpen(false)}>完成关闭</button></Dialog>}</CanvasSurfaceMotion>; }
    render(<DialogFixture/>); await act(async () => {}); expect(screen.getByRole('dialog')).toBeInTheDocument();
    fireEvent.click(screen.getByText('完成关闭')); expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.querySelector('[data-canvas-surface-exit="dialog"]')).toHaveAttribute('inert');
    expect(document.body.style.pointerEvents).not.toBe('none');
    const target = screen.getByText('画布目标'); target.focus(); expect(target).toHaveFocus();
  });
  it.each(['image', 'video'])('closes nested %s previews immediately inside the canvas scope', async kind => {
    function PreviewFixture() { const [open, setOpen] = useState(true); return <CanvasSurfaceMotion identity="u" reduced={false}>{open && (kind === 'image' ? <ImagePreviewModal imageUrl="/test.png" onClose={() => setOpen(false)}/> : <VideoPreviewModal videoUrl="/test.mp4" onClose={() => setOpen(false)}/>)}</CanvasSurfaceMotion>; }
    render(<PreviewFixture/>); await act(async () => {}); expect(document.querySelector('[data-canvas-preview]')).not.toBeNull();
    fireEvent.keyDown(window, { key: 'Escape' }); expect(document.querySelector('[data-canvas-preview]:not([data-canvas-surface-exit])')).toBeNull();
    expect(document.querySelector('[data-canvas-surface-exit]')).toHaveAttribute('inert');
  });
  it('uses shared entry and shorter exit timings and preserves live focus', () => {
    render(<Fixture/>); expect(animate).toHaveBeenCalledWith(surfaceFrames('panel'), expect.objectContaining({ duration: 180 }));
    const button = screen.getByText('切换'); button.focus(); fireEvent.click(button);
    expect(screen.queryByRole('complementary')).toBeNull(); const ghost = document.querySelector('[data-canvas-surface-exit="panel"]');
    expect(ghost).toHaveAttribute('inert'); expect(ghost).toHaveAttribute('aria-hidden', 'true'); expect(ghost).not.toHaveAttribute('id'); expect(button).toHaveFocus();
    expect(animate).toHaveBeenLastCalledWith(surfaceFrames('panel', true), expect.objectContaining({ duration: 120 }));
    act(() => vi.advanceTimersByTime(150)); expect(document.querySelector('[data-canvas-surface-exit]')).toBeNull();
  });
  it('captures the latest unsaved input value without a duplicate active form', () => {
    render(<Fixture/>); screen.getByRole('complementary').scrollTop = 75; fireEvent.change(screen.getByLabelText('内容'), { target: { value: '刚输入的内容' } }); fireEvent.click(screen.getByText('切换'));
    expect(document.querySelector('[data-canvas-surface-exit]')?.scrollTop).toBe(75); expect((document.querySelector('[data-canvas-surface-exit] input') as HTMLInputElement).value).toBe('刚输入的内容'); expect(screen.queryByRole('textbox')).toBeNull();
  });
  it('cancels old departures when reopened and cleans every snapshot on scope disposal', () => {
    const { unmount } = render(<Fixture/>); fireEvent.click(screen.getByText('切换')); expect(document.querySelector('[data-canvas-surface-exit]')).not.toBeNull();
    fireEvent.click(screen.getByText('切换')); expect(document.querySelector('[data-canvas-surface-exit]')).toBeNull();
    fireEvent.click(screen.getByText('切换')); unmount(); expect(document.querySelector('[data-canvas-surface-exit]')).toBeNull(); expect(vi.getTimerCount()).toBe(0);
  });
  it('does not leave prior-account content after identity changes', () => {
    const { rerender } = render(<Fixture/>); fireEvent.click(screen.getByText('切换')); rerender(<Fixture identity="other:org"/>);
    expect(document.querySelector('[data-canvas-surface-exit]')).toBeNull();
  });
  it('skips reduced motion and immediately clears active departures when preference changes', () => {
    media.matches = true; const { unmount } = render(<Fixture reduced/>); expect(animate).not.toHaveBeenCalled(); fireEvent.click(screen.getByText('切换')); expect(document.querySelector('[data-canvas-surface-exit]')).toBeNull(); unmount();
    media.matches = false; const { rerender } = render(<Fixture/>); fireEvent.click(screen.getByText('切换'));
    act(() => { media.matches = true; listeners.forEach(fn => fn()); }); rerender(<Fixture reduced/>); expect(document.querySelector('[data-canvas-surface-exit]')).toBeNull();
  });
});
