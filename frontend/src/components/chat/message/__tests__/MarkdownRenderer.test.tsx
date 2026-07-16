/**
 * MarkdownRenderer 集成测试
 *
 * 重点验证 V3 Phase 14 之后加的"中文伪 LaTeX 转义"集成是否生效：
 * - 含中文的 $...$ 不应该被 KaTeX 渲染（应该是普通文本）
 * - 真公式（不含中文）保留 KaTeX 渲染
 *
 * 不测：
 * - KaTeX 自身的数学公式渲染（第三方库已测）
 * - framer-motion 动画（motion-mock 跳过）
 * - highlight.js 自身的语法规则（第三方库已测）
 */

import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import MarkdownRenderer from '../MarkdownRenderer';

const { downloadFileMock } = vi.hoisted(() => ({ downloadFileMock: vi.fn() }));

vi.mock('../../../../utils/downloadFile', () => ({
  downloadFile: downloadFileMock,
}));

vi.mock('../MermaidBlock', () => ({
  default: ({ children }: { children: string }) => (
    <div data-testid="mermaid-source">{children}</div>
  ),
}));

describe('MarkdownRenderer — 中文伪 LaTeX 转义集成', () => {
  it('含中文的 $...$ 不渲染为 KaTeX 节点（普通文本显示）', () => {
    const content = '费用 $金额$ 元';
    const { container } = render(<MarkdownRenderer content={content} />);

    // KaTeX 渲染的节点会有 .katex class，中文公式不应该产生这些节点
    const katexNodes = container.querySelectorAll('.katex');
    expect(katexNodes.length).toBe(0);

    // 原文 "金额" 应该作为普通文本存在（可能拆分在多个节点里）
    expect(container.textContent).toContain('金额');
  });

  it('真数学公式 $E=mc^2$ 被 KaTeX 渲染', () => {
    const content = '爱因斯坦公式 $E=mc^2$ 描述质能关系';
    const { container } = render(<MarkdownRenderer content={content} />);

    // 真公式会产生 KaTeX DOM 节点
    const katexNodes = container.querySelectorAll('.katex');
    expect(katexNodes.length).toBeGreaterThan(0);
  });

  it('混合场景：真公式 + 中文伪公式 各自正确处理', () => {
    const content = '能量 $E=mc^2$ 描述质能，成本 $金额$ 表示费用';
    const { container } = render(<MarkdownRenderer content={content} />);

    // 应该有且仅有 1 个 KaTeX 渲染（真公式）
    const katexNodes = container.querySelectorAll('.katex');
    expect(katexNodes.length).toBeGreaterThan(0);

    // 中文文本 "金额" 仍然可见
    expect(container.textContent).toContain('金额');
  });

  it('纯文本（无 Markdown 语法）走快速路径，不调 react-markdown', () => {
    const content = '这是纯文本消息，没有任何 Markdown 标记';
    const { container } = render(<MarkdownRenderer content={content} />);

    // 快速路径下不渲染 markdown-body 容器
    expect(container.querySelector('.markdown-body')).toBeNull();
    // 内容正确显示
    expect(container.textContent).toContain('这是纯文本消息');
  });

  it('空 content 安全渲染，不崩溃', () => {
    const { container } = render(<MarkdownRenderer content="" />);
    expect(container).toBeTruthy();
  });

  it('JSON 代码块保留原文且不产生 object Object', () => {
    const json = '{\n  "name": "test",\n  "items": [{"id": 1}]\n}';
    const { container } = render(
      <MarkdownRenderer content={`\`\`\`json\n${json}\n\`\`\``} />,
    );

    expect(container.textContent).toContain('"name": "test"');
    expect(container.textContent).toContain('"items": [{"id": 1}]');
    expect(container.textContent).not.toContain('[object Object]');
  });

  it('代码中的 HTML 只作为文本显示', () => {
    const { container } = render(
      <MarkdownRenderer content={'```html\n<script>alert("xss")</script>\n```'} />,
    );

    expect(container.querySelector('script')).toBeNull();
    expect(container.textContent).toContain('<script>alert("xss")</script>');
  });

  it('普通链接使用新窗口安全属性', () => {
    render(<MarkdownRenderer content="[官网](https://example.com/page)" />);

    const link = screen.getByRole('link', { name: '官网' });
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('文件链接拦截浏览器导航并调用统一下载入口', () => {
    render(<MarkdownRenderer content="[报告](https://example.com/%E6%8A%A5%E5%91%8A.pdf)" />);

    fireEvent.click(screen.getByRole('link', { name: '报告' }));
    expect(downloadFileMock).toHaveBeenCalledWith(
      'https://example.com/%E6%8A%A5%E5%91%8A.pdf',
      '报告.pdf',
    );
  });

  it('Mermaid 组件收到未经 React 节点转换的原始源码', async () => {
    render(<MarkdownRenderer content={'```mermaid\ngraph TD\nA-->B\n```'} />);

    await waitFor(() => {
      expect(screen.getByTestId('mermaid-source').textContent).toBe('graph TD\nA-->B');
    });
  });

  it('表格图片加载失败后回退显示原始 URL', () => {
    const url = 'https://example.com/image.png';
    const { container } = render(
      <MarkdownRenderer content={`| 图片 |\n|---|\n| ${url} |`} />,
    );
    const image = screen.getByRole('img', { name: '缩略图' });

    fireEvent.error(image);
    expect(container.textContent).toContain(url);
    expect(screen.queryByRole('img', { name: '缩略图' })).toBeNull();
  });

  it('流式纯文本显示光标并移除末尾空白', () => {
    const { container } = render(
      <MarkdownRenderer content={'正在生成\n\n'} isStreaming className="custom" />,
    );

    expect(container.firstElementChild).toHaveClass('custom');
    expect(container.textContent).toBe('正在生成');
    expect(container.querySelector('.animate-cursor-blink')).toBeInTheDocument();
  });

  it('行内代码保持文本节点且普通表格单元格不转图片', () => {
    const { container } = render(
      <MarkdownRenderer content={'使用 `const value = 1`\n\n| 值 |\n|---|\n| 普通文本 |'} />,
    );

    expect(container.querySelector('code')).toHaveTextContent('const value = 1');
    expect(container.querySelector('.markdown-table-wrapper')).toBeInTheDocument();
    expect(container.querySelector('.table-thumbnail')).toBeNull();
  });

  it('识别无图片后缀的受信 CDN 图片 URL', () => {
    render(
      <MarkdownRenderer content={'| 图片 |\n|---|\n| https://img.alicdn.com/asset?id=1 |'} />,
    );

    expect(screen.getByRole('img', { name: '缩略图' })).toHaveAttribute(
      'src',
      'https://img.alicdn.com/asset?id=1',
    );
  });

  it('workspace 文件路径使用统一下载入口', () => {
    render(<MarkdownRenderer content="[数据](/api/workspace/files/data)" />);

    fireEvent.click(screen.getByRole('link', { name: '数据' }));
    expect(downloadFileMock).toHaveBeenCalledWith('/api/workspace/files/data', 'data');
  });
});
