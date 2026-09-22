import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import MarkdownRenderer from '../MarkdownRenderer';
import { TableBlock } from '../TableBlock';
import { presentationColors } from '../../../../utils/messagePresentation';
import { parseContentPart } from '../../../../schemas/messageProtocol';

describe('AI presentation contract', () => {
  it('honors explicit colors independent of direction and supports prose', () => {
    const { container } = render(<MarkdownRenderer content={'<span data-color="blue">重点</span>\n\n| 指标 | 涨跌 |\n|---|---|\n| 销售 | <span data-color="green">↑ +10%</span> |\n| 退款 | <span data-color="red">↑ +10%</span> |\n| 未指定 | ↑ +10% |'} />);
    expect(container.querySelector('[data-color="blue"]')).toHaveTextContent('重点');
    expect(container.querySelector('[data-color="green"]')).toHaveTextContent('↑ +10%');
    expect(container.querySelector('[data-color="red"]')).toHaveTextContent('↑ +10%');
    expect(container.querySelector('tbody tr:last-child td:last-child')).not.toHaveAttribute('class');
    expect(container.querySelector('tbody tr:last-child [data-color]')).toBeNull();
  });

  it('preserves explicit style through the wire boundary and structured table', () => {
    const part = parseContentPart({ type: 'table', columns: ['数值'], rows: [{ 数值: 10 }], cell_styles: [{ 数值: { color: 'blue', bold: true } }] });
    if (part?.type !== 'table') throw new Error('table rejected');
    const { container } = render(<TableBlock {...part} />);
    expect(container.querySelector('[data-color="blue"]')).toHaveTextContent('10');
    expect(container.querySelector('strong')).toHaveTextContent('10');
  });
  it.each(Object.keys(presentationColors))('renders shared palette color %s in text and table', (color) => {
    const { container } = render(<MarkdownRenderer content={`<span data-color="${color}">强调</span>`} />);
    const span = container.querySelector('[data-color]') as HTMLElement;
    expect(span).toHaveAttribute('data-color', color);
    expect(span.style.getPropertyValue('--message-color-light')).toBe(presentationColors[color as keyof typeof presentationColors].light);
    expect(span.style.getPropertyValue('--message-color-dark')).toBe(presentationColors[color as keyof typeof presentationColors].dark);
  });

  it.each(['- ', '1. ', '# ', '> ', '**', '*'])('supports inline color inside Markdown context %s', (prefix) => {
    const closing = prefix === '**' || prefix === '*' ? prefix : '';
    const { container } = render(<MarkdownRenderer content={`${prefix}<span data-color="blue">强调</span>${closing}`} />);
    expect(container.querySelector('[data-color="blue"]')).toHaveTextContent('强调');
    expect(container.textContent).not.toContain('<span');
  });

  it('keeps nested colors and scopes unfinished spans to a cell during streaming', () => {
    const content = '| A | B |\n|---|---|\n| <span data-color="blue">**外层** <span data-color="red">内层</span> 尾部 | 普通 |';
    const { container, rerender } = render(<MarkdownRenderer content={content} isStreaming />);
    expect(container.querySelector('[data-color="blue"] strong')).toHaveTextContent('外层');
    expect(container.querySelector('[data-color="blue"] [data-color="red"]')).toHaveTextContent('内层');
    expect(container.querySelector('td:last-child [data-color]')).toBeNull();
    rerender(<MarkdownRenderer content={'<span data-color="green">$E=mc^2$</span>'} />);
    expect(container.querySelector('[data-color="green"] .katex .katex-html')).toBeInTheDocument();
  });

  it('removes unsupported styles without executing HTML, and does not let legacy styles override invalid canonical colors', () => {
    const { container } = render(<MarkdownRenderer content={'结果 <span data-color="red" onclick="alert(1)" class="overlay" style="position:fixed;background:url(https://invalid.example)">合法颜色</span> <span data-color="unknown" style="color:red">未知颜色</span> <span style="color:var(--evil)">未知样式</span> <svg onload="alert(1)"></svg>'} />);
    expect(container.querySelector('[data-color="red"]')).toHaveTextContent('合法颜色');
    expect(container.querySelectorAll('[data-color]')).toHaveLength(1);
    expect(container.querySelector('[onclick], .overlay, svg, [onload]')).toBeNull();
    expect(container.innerHTML).not.toContain('position: fixed');
    expect(container.textContent).toContain('未知颜色');
    expect(container.textContent).toContain('未知样式');
  });

  it.each(['red', '#ff0000', 'rgb(255, 0, 0)'])('maps legacy CSS %s without forwarding other CSS', (color) => {
    const { container } = render(<MarkdownRenderer content={`结果 <span style="color:${color};position:fixed">10%</span>`} />);
    const span = container.querySelector('[data-color="red"]') as HTMLElement;
    expect(span).toHaveTextContent('10%');
    expect(span.style.position).toBe('');
  });

  it.each([
    [{ 数值: { color: 'url(evil)' } }],
    [{ 数值: { color: 'red', onclick: 'evil' } }],
    [{ 数值: { bold: 'true' } }],
    'invalid',
    null,
  ])('degrades invalid optional metadata while retaining table data (%j)', (cell_styles) => {
    const part = parseContentPart({ type: 'table', columns: ['数值'], rows: [{ 数值: 10 }], cell_styles });
    if (part?.type !== 'table') throw new Error('table data lost');
    const { container } = render(<TableBlock {...part} />);
    expect(container.querySelector('td')).toHaveTextContent('10');
    expect(container.querySelector('[data-color]')).toBeNull();
  });

  it('keeps styled table values literal, including HTML and Markdown data', () => {
    const { container } = render(<TableBlock columns={['值']} rows={[{ 值: '<script>alert(1)</script> **原文**' }]} cell_styles={[{ 值: { color: 'purple' } }]} />);
    expect(container.querySelector('[data-color="purple"]')).toHaveTextContent('<script>alert(1)</script> **原文**');
    expect(container.querySelector('script, strong')).toBeNull();
  });
});
