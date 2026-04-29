/**
 * ToolStepCard 组件单元测试
 *
 * 覆盖：3 种状态渲染、折叠展开、code/output 展示、工具名映射、耗时格式化
 */

import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ToolStepCard from '../ToolStepCard';

describe('ToolStepCard', () => {
  const baseProps = {
    toolName: 'web_search',
    toolCallId: 'tc_1',
    status: 'completed' as const,
    summary: '找到3条结果',
    elapsedMs: 1500,
  };

  describe('状态渲染', () => {
    it('running 状态显示"执行中"', () => {
      render(<ToolStepCard {...baseProps} status="running" />);
      expect(screen.getByText('执行中')).toBeDefined();
    });

    it('completed 状态显示耗时', () => {
      render(<ToolStepCard {...baseProps} status="completed" elapsedMs={2300} />);
      expect(screen.getByText('2.3s')).toBeDefined();
    });

    it('error 状态显示"失败"', () => {
      render(<ToolStepCard {...baseProps} status="error" />);
      expect(screen.getByText('失败')).toBeDefined();
    });

    it('毫秒级耗时显示 ms 单位', () => {
      render(<ToolStepCard {...baseProps} elapsedMs={500} />);
      expect(screen.getByText('500ms')).toBeDefined();
    });
  });

  describe('折叠展开', () => {
    it('completed 状态默认折叠，点击展开显示 summary', () => {
      render(<ToolStepCard {...baseProps} />);
      // 默认折叠，summary 不可见
      expect(screen.queryByText('找到3条结果')).toBeNull();

      // 点击展开
      fireEvent.click(screen.getByRole('button'));
      expect(screen.getByText('找到3条结果')).toBeDefined();
    });

    it('running 状态不可展开', () => {
      render(<ToolStepCard {...baseProps} status="running" summary="进行中" />);
      fireEvent.click(screen.getByRole('button'));
      // 点击后仍然看不到 summary
      expect(screen.queryByText('进行中')).toBeNull();
    });

    it('无 summary 时不可展开', () => {
      render(<ToolStepCard {...baseProps} summary={undefined} />);
      fireEvent.click(screen.getByRole('button'));
      // 没有折叠内容区
      expect(screen.queryByText('代码')).toBeNull();
    });
  });

  describe('code_execute 展示', () => {
    it('展开后显示代码和输出', () => {
      render(
        <ToolStepCard
          toolName="code_execute"
          toolCallId="tc_2"
          status="completed"
          code="import pandas as pd\ndf = pd.read_csv('data.csv')"
          output="处理了120条数据"
          elapsedMs={5100}
        />,
      );
      // 展开
      fireEvent.click(screen.getByRole('button'));
      expect(screen.getByText('代码')).toBeDefined();
      expect(screen.getByText('输出')).toBeDefined();
      expect(screen.getByText('处理了120条数据')).toBeDefined();
    });

    it('code_execute 使用 💻 图标', () => {
      const { container } = render(
        <ToolStepCard toolName="code_execute" toolCallId="tc_3" status="running" />,
      );
      expect(container.textContent).toContain('💻');
    });
  });

  describe('工具名映射', () => {
    it('erp_trade_query 显示"查询订单信息"（去掉"正在"前缀）', () => {
      render(<ToolStepCard toolName="erp_trade_query" toolCallId="tc_4" status="completed" elapsedMs={1000} />);
      expect(screen.getByText('查询订单信息')).toBeDefined();
    });

    it('未知工具名显示 "执行 {toolName}"', () => {
      render(<ToolStepCard toolName="unknown_tool" toolCallId="tc_5" status="completed" elapsedMs={100} />);
      expect(screen.getByText('执行 unknown_tool')).toBeDefined();
    });

    it('非 code_execute 使用 🔧 图标', () => {
      const { container } = render(
        <ToolStepCard toolName="web_search" toolCallId="tc_6" status="running" />,
      );
      expect(container.textContent).toContain('🔧');
    });
  });
});
