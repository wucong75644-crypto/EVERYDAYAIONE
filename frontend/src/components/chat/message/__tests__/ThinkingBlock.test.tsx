/**
 * ThinkingBlock 组件单元测试
 *
 * 覆盖：durationMs 优先级、自动展开/折叠、内容渲染、空内容不渲染
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import ThinkingBlock from '../ThinkingBlock';

describe('ThinkingBlock', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('渲染基础', () => {
    it('有内容时渲染组件', () => {
      const { container } = render(<ThinkingBlock content="推理过程" />);
      expect(container.innerHTML).not.toBe('');
    });

    it('空内容且非思考中时不渲染', () => {
      const { container } = render(<ThinkingBlock content="" isThinking={false} />);
      expect(container.innerHTML).toBe('');
    });

    it('空内容但思考中时渲染', () => {
      const { container } = render(<ThinkingBlock content="" isThinking={true} />);
      expect(container.innerHTML).not.toBe('');
    });
  });

  describe('时长显示', () => {
    it('优先使用后端 durationMs', () => {
      render(<ThinkingBlock content="推理" durationMs={8000} />);
      expect(screen.getByText('用时 8秒')).toBeDefined();
    });

    it('durationMs 为 null 时 fallback 到 thinkingStartTime', () => {
      const startTime = Date.now() - 5000;
      render(<ThinkingBlock content="推理" thinkingStartTime={startTime} />);
      expect(screen.getByText(/用时/)).toBeDefined();
    });

    it('思考中不显示时长', () => {
      render(<ThinkingBlock content="推理" isThinking={true} durationMs={5000} />);
      expect(screen.queryByText(/用时/)).toBeNull();
    });

    it('超过 60 秒显示分钟格式', () => {
      render(<ThinkingBlock content="推理" durationMs={90000} />);
      expect(screen.getByText('用时 1分30秒')).toBeDefined();
    });
  });

  describe('默认折叠', () => {
    it('isThinking=true 时也默认折叠', () => {
      render(<ThinkingBlock content="推理过程" isThinking={true} />);
      // 默认折叠，内容不可见
      expect(screen.queryByText('推理过程')).toBeNull();
      // 但显示 thinking 动画标签
      expect(screen.getByText('thinking')).toBeDefined();
    });

    it('DB 加载（isThinking=false）时保持折叠', () => {
      render(<ThinkingBlock content="历史推理" durationMs={3000} />);
      // 折叠态不显示内容
      expect(screen.queryByText('历史推理')).toBeNull();
      // 但显示 Thought for
      expect(screen.getByText(/Thought for/)).toBeDefined();
    });
  });

  describe('手动展开/折叠', () => {
    it('点击可切换展开/折叠', () => {
      render(<ThinkingBlock content="推理过程" durationMs={5000} />);
      const button = screen.getByRole('button');

      // 默认折叠
      expect(screen.queryByText('推理过程')).toBeNull();

      // 点击展开
      fireEvent.click(button);
      expect(screen.getByText('推理过程')).toBeDefined();

      // 再次点击折叠
      fireEvent.click(button);
      // AnimatePresence exit 后内容消失
    });
  });
});
