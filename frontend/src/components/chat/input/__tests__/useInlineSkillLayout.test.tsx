import { useRef } from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useInlineSkillLayout } from '../useInlineSkillLayout';

let lastRect: Partial<DOMRect>;
let resize: ResizeObserverCallback;
const disconnect = vi.fn();
function Harness({ tags = 1 }: { tags?: number }) {
  const textarea = useRef<HTMLTextAreaElement>(null);
  const prefix = useRef<HTMLDivElement>(null);
  const reveal = useInlineSkillLayout(textarea, prefix);
  return <div>
    <div ref={prefix} data-testid="prefix" onFocusCapture={reveal}>
      {Array.from({ length: tags }, (_, index) => <button data-skill-tag key={index}>Skill {index}</button>)}
    </div>
    <textarea ref={textarea} defaultValue="正文与 Skill 身份分开" />
  </div>;
}

beforeEach(() => {
  lastRect = { top: 8, left: 10, right: 170, width: 160, height: 24 };
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function () {
    return { top: 8, left: 10, right: 310, width: 300, height: 44,
      ...(this.hasAttribute('data-skill-tag') ? lastRect : {}), toJSON: () => ({}) } as DOMRect;
  });
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(220);
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: ResizeObserverCallback) { resize = callback; }
    observe() {}
    disconnect = disconnect;
  });
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('native input layout for inline Skills', () => {
  it('reserves only the first-line prefix and remeasures when its size changes', () => {
    const view = render(<Harness />);
    const textarea = screen.getByRole('textbox');
    expect(textarea).toHaveStyle({ textIndent: '168px', height: '120px' });
    expect(textarea.style.paddingTop).toBe('');
    lastRect = { ...lastRect, width: 180, right: 190 };
    act(() => resize([], {} as ResizeObserver));
    expect(textarea).toHaveStyle({ textIndent: '188px' });
    view.rerender(<Harness tags={0} />);
    expect(textarea.style.textIndent).toBe('');
    expect(textarea).toHaveValue('正文与 Skill 身份分开');
  });

  it('starts text after the last wrapped tag row and restores normal sizing after removal', () => {
    lastRect = { ...lastRect, top: 36, right: 150, width: 140 };
    const view = render(<Harness tags={3} />);
    const textarea = screen.getByRole('textbox');
    expect(textarea).toHaveStyle({ textIndent: '148px', paddingTop: '36px', maxHeight: '148px', height: '148px' });
    view.rerender(<Harness tags={0} />);
    expect(textarea.style.paddingTop).toBe('');
    expect(textarea.style.maxHeight).toBe('');
    expect(textarea).toHaveStyle({ height: '120px' });
  });

  it('scrolls the prefix with the first line and reveals it for keyboard access', () => {
    const view = render(<Harness />);
    const textarea = screen.getByRole('textbox');
    const prefix = screen.getByTestId('prefix');
    fireEvent.scroll(textarea, { target: { scrollTop: 48 } });
    expect(prefix).toHaveStyle({ transform: 'translateY(-48px)' });
    fireEvent.focus(screen.getByRole('button', { name: 'Skill 0' }));
    expect(textarea.scrollTop).toBe(0);
    expect(prefix).toHaveStyle({ transform: 'translateY(-0px)' });
    view.unmount();
    expect(disconnect).toHaveBeenCalled();
  });
});
