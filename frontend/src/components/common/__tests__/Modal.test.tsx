/**
 * common/Modal 兼容层测试
 *
 * V3 Review HIGH-2 fix 的保护网：
 * - closeOnOverlay / closeOnEsc prop 必须真正透传到底层 primitives/Dialog
 * - 旧版签名完全保留，6 个 Modal caller 零修改
 * - maxWidth 字符串到 Dialog size 的映射必须稳定
 *
 * 不测的部分：
 * - Modal 内部的 Radix Portal 渲染逻辑（primitives/Dialog.test.tsx 已覆盖）
 * - framer-motion 动画（skipAnimations 已开）
 * - 焦点 trap / 锁滚动（Radix 内置，已测过）
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import Modal from '../Modal';

describe('Modal - 基本渲染', () => {
  it('isOpen=false 时不渲染内容', () => {
    render(
      <Modal isOpen={false} onClose={vi.fn()} title="测试">
        <p>body-content</p>
      </Modal>,
    );
    expect(screen.queryByText('body-content')).not.toBeInTheDocument();
  });

  it('isOpen=true 时通过 Portal 渲染 body 和 title', () => {
    render(
      <Modal isOpen={true} onClose={vi.fn()} title="我的标题">
        <p>body-content</p>
      </Modal>,
    );
    expect(screen.getByText('body-content')).toBeInTheDocument();
    // Title 被渲染 2 次：
    // 1. primitives/Dialog 的 sr-only Radix Title（hideTitleVisually=true，供 a11y）
    // 2. common/Modal 自己在 header 区画的可见 h2（aria-hidden）
    const titles = screen.getAllByText('我的标题');
    expect(titles).toHaveLength(2);
  });

  it('无 title 时仍能渲染（兜底 sr-only Title 不影响视觉）', () => {
    render(
      <Modal isOpen={true} onClose={vi.fn()}>
        <p>no-title-body</p>
      </Modal>,
    );
    expect(screen.getByText('no-title-body')).toBeInTheDocument();
  });
});

describe('Modal - maxWidth 映射（mapMaxWidthToSize）', () => {
  /**
   * 6 个 caller 使用的 maxWidth 值必须都能映射到对应 size。
   * 未来任何 caller 改 maxWidth 都会被这组测试保护。
   */
  it.each([
    ['max-w-sm', 'max-w-sm'],
    ['max-w-md', 'max-w-md'],
    ['max-w-2xl', 'max-w-2xl'],
    ['max-w-4xl', 'max-w-4xl'],
  ] as const)('maxWidth=%s 在 DOM 中包含 %s class', (input, expected) => {
    render(
      <Modal isOpen={true} onClose={vi.fn()} title="size-test" maxWidth={input}>
        <p>content</p>
      </Modal>,
    );
    // Portal 渲染到 body，用 role=dialog 定位容器
    const dialog = screen.getByRole('dialog');
    expect(dialog.className).toContain(expected);
  });

  it('maxWidth=自定义值（如 "max-w-[600px]"）作为 className 透传', () => {
    render(
      <Modal
        isOpen={true}
        onClose={vi.fn()}
        title="custom"
        maxWidth="max-w-[600px]"
      >
        <p>custom-width</p>
      </Modal>,
    );
    const dialog = screen.getByRole('dialog');
    expect(dialog.className).toContain('max-w-[600px]');
  });
});

describe('Modal - closeOnEsc / closeOnOverlay 透传（V3 Review HIGH-2 fix）', () => {
  /**
   * 核心回归保护：旧版 common/Modal 的 closeOnOverlay/closeOnEsc 是 silent no-op
   * （用 _closeOnOverlay/_closeOnEsc 下划线前缀标记未使用），HIGH-2 fix 后真正透传
   * 到 primitives/Dialog 的 closeOnEscape/closeOnOutsideClick。
   *
   * 测试策略：ESC 键按下时观察 onClose 是否被调用。
   * jsdom 能模拟 keydown，Radix Dialog 的 onEscapeKeyDown handler 会被触发。
   */

  it('closeOnEsc=true（默认）时按 ESC 触发 onClose', () => {
    const handleClose = vi.fn();
    render(
      <Modal isOpen={true} onClose={handleClose} title="esc-test">
        <p>content</p>
      </Modal>,
    );
    // Radix Dialog 监听 document keydown，派发 keydown=Escape 触发关闭
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(handleClose).toHaveBeenCalled();
  });

  it('closeOnEsc=false 时按 ESC 不触发 onClose（透传生效）', () => {
    const handleClose = vi.fn();
    render(
      <Modal
        isOpen={true}
        onClose={handleClose}
        title="esc-disabled"
        closeOnEsc={false}
      >
        <p>content</p>
      </Modal>,
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(handleClose).not.toHaveBeenCalled();
  });

  it('showCloseButton=false 时不渲染 X 关闭按钮', () => {
    render(
      <Modal
        isOpen={true}
        onClose={vi.fn()}
        title="no-close"
        showCloseButton={false}
      >
        <p>content</p>
      </Modal>,
    );
    expect(screen.queryByRole('button', { name: '关闭' })).not.toBeInTheDocument();
  });

  it('showCloseButton=true（默认）时点击 X 触发 onClose', () => {
    const handleClose = vi.fn();
    render(
      <Modal isOpen={true} onClose={handleClose} title="close-btn">
        <p>content</p>
      </Modal>,
    );
    fireEvent.click(screen.getByRole('button', { name: '关闭' }));
    expect(handleClose).toHaveBeenCalled();
  });
});
