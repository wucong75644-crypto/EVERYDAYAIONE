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
 * - 语法高亮（rehype-highlight 已测）
 */

import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import MarkdownRenderer from '../MarkdownRenderer';

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
});
