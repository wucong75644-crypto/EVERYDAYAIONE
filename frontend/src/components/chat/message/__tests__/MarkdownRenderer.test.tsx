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


describe('MarkdownRenderer — 分析结果中的 HTML 格式兼容', () => {
  const rows = [
    ['系统单', '2', '1', '+1', '↑ +100.0%', 'red'],
    ['京东', '174', '158', '+16', '↑ +10.1%', 'red'],
    ['抖音', '373', '372', '+1', '↑ +0.3%', 'red'],
    ['淘宝', '422', '430', '-8', '↓ -1.9%', 'green'],
    ['1688', '126', '136', '-10', '↓ -7.4%', 'green'],
    ['拼多多', '5,336', '5,798', '-462', '↓ -8.0%', 'green'],
    ['小红书', '21', '25', '-4', '↓ -16.0%', 'green'],
    ['快手', '8', '28', '-20', '↓ -71.4%', 'green'],
  ];
  const header = '| 平台 | 今日有效订单 | 昨日有效订单 | 涨跌量 | 涨跌幅 |\n|---|---|---|---|---|';

  it('截图中的对比表保留所有数值和箭头，不显示 HTML 标签', () => {
    const content = [header, ...rows.map(([platform, today, yesterday, delta, rate, color]) =>
      `| ${platform} | ${today} | ${yesterday} | ${delta} | <span style="color:${color}">${rate}</span> |`,
    )].join('\n');
    const { container } = render(<MarkdownRenderer content={content} />);

    const renderedRows = Array.from(container.querySelectorAll('tbody tr'));
    expect(renderedRows).toHaveLength(rows.length);
    renderedRows.forEach((row, index) => {
      expect(Array.from(row.querySelectorAll('td'), (cell) => cell.textContent))
        .toEqual(rows[index].slice(0, 5));
    });
    expect(container.textContent).not.toMatch(/<\/?span|style=/);
    expect(container.querySelector('td span[style]')).toBeNull();
  });

  it('流式标签未闭合时也显示数值，完成后结果一致', () => {
    const content = `${header}\n| 京东 | 174 | 158 | +16 | <span style="color:red">↑ +10.1%`;
    const { container, rerender } = render(<MarkdownRenderer content={content} isStreaming />);

    expect(container.querySelector('tbody tr td:last-child')?.textContent).toBe('↑ +10.1%');
    rerender(<MarkdownRenderer content={`${content}</span> |`} />);
    expect(container.querySelector('tbody tr td:last-child')?.textContent).toBe('↑ +10.1%');
  });

  it('正文行内标签降级为内容，Markdown 强调和比较符号保持可见', () => {
    const { container } = render(
      <MarkdownRenderer content={'涨跌：<span style="color:red">**↑ +10.1%**</span>，1 < 2，3 > 2'} />,
    );

    expect(container.textContent).toBe('涨跌：↑ +10.1%，1 < 2，3 > 2');
    expect(container.querySelector('strong')).toHaveTextContent('↑ +10.1%');
  });

  it('HTML 代码示例和显式转义文字仍原样显示', () => {
    const html = '<span style="color:red">↑ +10.1%</span>';
    const content = `行内示例：\`${html}\`\n\n\`\`\`html\n${html}\n\`\`\`\n\n&lt;span&gt;示例&lt;/span&gt;`;
    const { container } = render(<MarkdownRenderer content={content} />);

    expect(Array.from(container.querySelectorAll('code'), (code) => code.textContent)).toEqual([html, html]);
    expect(container.textContent).toContain('<span>示例</span>');
    expect(container.querySelector('span[style]')).toBeNull();
  });

  it('不丢弃完整 HTML 块中的数据，继续按源码显示', () => {
    const html = '<div>总数：174，<span style="color:red">↑ +10.1%</span></div>';
    const { container } = render(<MarkdownRenderer content={html} />);

    expect(container.textContent).toBe(html);
    expect(container.querySelector('span[style]')).toBeNull();
  });

  it('分析文字中的外部 HTML 不生成可执行 DOM 或注入属性', () => {
    const { container } = render(
      <MarkdownRenderer content={'结果：<span onclick="alert(1)" style="position:fixed">↑ +10.1%</span><img src="x" onerror="alert(1)">\n\n<script>alert(1)</script>'} />,
    );

    expect(container.textContent).toContain('结果：↑ +10.1%');
    expect(container.textContent).not.toMatch(/<\/?span|onclick|position:fixed/);
    expect(container.querySelector('script, img, [onclick], [onerror], [style]')).toBeNull();
  });
});
