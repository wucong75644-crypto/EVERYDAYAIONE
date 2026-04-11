/**
 * SearchPanel 行为测试
 *
 * 覆盖核心交互（防抖 / 取消旧请求 / 状态切换 / 跳转 / 关闭），
 * 不测纯视觉部分（动画由 motion-mock 跳过）。
 *
 * Mock 策略：
 * - mock services/message.searchMessages 控制返回值和延迟
 * - 用 vi.useFakeTimers 控制 300ms 防抖
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import SearchPanel from '../SearchPanel';
import { searchMessages } from '../../../../services/message';

vi.mock('../../../../services/message', () => ({
  searchMessages: vi.fn(),
}));

const mockSearchMessages = vi.mocked(searchMessages);

/** 制造一条 raw API message（带 content 数组结构） */
function makeRawMsg(id: string, text: string, role = 'user') {
  return {
    id,
    conversation_id: 'conv-1',
    role,
    content: [{ type: 'text', text }],
    status: 'completed',
    created_at: '2026-04-11T10:00:00Z',
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('SearchPanel — 渲染开关', () => {
  it('isOpen=false 时不渲染 dialog', () => {
    render(
      <SearchPanel
        isOpen={false}
        onClose={vi.fn()}
        conversationId="conv-1"
        onJumpToMessage={vi.fn()}
      />,
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('isOpen=true 时渲染搜索面板（含输入框 + 关闭按钮）', () => {
    render(
      <SearchPanel
        isOpen={true}
        onClose={vi.fn()}
        conversationId="conv-1"
        onJumpToMessage={vi.fn()}
      />,
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByLabelText('搜索关键词')).toBeInTheDocument();
    expect(screen.getByLabelText('关闭搜索')).toBeInTheDocument();
  });

  it('初始状态显示空提示文案', () => {
    render(
      <SearchPanel
        isOpen={true}
        onClose={vi.fn()}
        conversationId="conv-1"
        onJumpToMessage={vi.fn()}
      />,
    );
    expect(screen.getByText('输入关键词搜索对话内消息')).toBeInTheDocument();
  });
});

describe('SearchPanel — 防抖搜索', () => {
  it('输入 query 后 300ms 内不调用 searchMessages', () => {
    mockSearchMessages.mockResolvedValue({
      messages: [],
      total: 0,
      query: '',
    });

    render(
      <SearchPanel
        isOpen={true}
        onClose={vi.fn()}
        conversationId="conv-1"
        onJumpToMessage={vi.fn()}
      />,
    );
    const input = screen.getByLabelText('搜索关键词') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '订单' } });

    // 300ms 内不应该有任何请求
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(mockSearchMessages).not.toHaveBeenCalled();
  });

  it('输入 query 300ms 后调用 searchMessages 一次', async () => {
    mockSearchMessages.mockResolvedValue({
      messages: [],
      total: 0,
      query: '订单',
    });

    render(
      <SearchPanel
        isOpen={true}
        onClose={vi.fn()}
        conversationId="conv-1"
        onJumpToMessage={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText('搜索关键词'), {
      target: { value: '订单' },
    });

    await act(async () => {
      vi.advanceTimersByTime(300);
    });

    expect(mockSearchMessages).toHaveBeenCalledTimes(1);
    expect(mockSearchMessages).toHaveBeenCalledWith(
      'conv-1',
      '订单',
      30,
      expect.any(AbortSignal),
    );
  });

  it('连续输入只触发最后一次（防抖取消旧 timer）', async () => {
    mockSearchMessages.mockResolvedValue({
      messages: [],
      total: 0,
      query: '订单 12 月',
    });

    render(
      <SearchPanel
        isOpen={true}
        onClose={vi.fn()}
        conversationId="conv-1"
        onJumpToMessage={vi.fn()}
      />,
    );
    const input = screen.getByLabelText('搜索关键词');
    // 三次连续输入
    fireEvent.change(input, { target: { value: '订' } });
    act(() => {
      vi.advanceTimersByTime(100);
    });
    fireEvent.change(input, { target: { value: '订单' } });
    act(() => {
      vi.advanceTimersByTime(100);
    });
    fireEvent.change(input, { target: { value: '订单 12 月' } });

    await act(async () => {
      vi.advanceTimersByTime(300);
    });

    // 只调用一次，且是最后的那个 query
    expect(mockSearchMessages).toHaveBeenCalledTimes(1);
    expect(mockSearchMessages).toHaveBeenCalledWith(
      'conv-1',
      '订单 12 月',
      30,
      expect.any(AbortSignal),
    );
  });

  it('空 query / 纯空白 query 不触发请求', async () => {
    render(
      <SearchPanel
        isOpen={true}
        onClose={vi.fn()}
        conversationId="conv-1"
        onJumpToMessage={vi.fn()}
      />,
    );
    const input = screen.getByLabelText('搜索关键词');
    fireEvent.change(input, { target: { value: '   \t  ' } });

    await act(async () => {
      vi.advanceTimersByTime(500);
    });

    expect(mockSearchMessages).not.toHaveBeenCalled();
  });
});

describe('SearchPanel — 结果状态', () => {
  it('搜索返回空数组时显示无结果提示', async () => {
    mockSearchMessages.mockResolvedValue({
      messages: [],
      total: 0,
      query: '不存在的内容',
    });

    render(
      <SearchPanel
        isOpen={true}
        onClose={vi.fn()}
        conversationId="conv-1"
        onJumpToMessage={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText('搜索关键词'), {
      target: { value: '不存在的内容' },
    });

    await act(async () => {
      vi.advanceTimersByTime(300);
      // 等微任务完成
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByText('没有匹配的消息')).toBeInTheDocument();
  });

  it('搜索有结果时渲染列表 + 关键词高亮', async () => {
    mockSearchMessages.mockResolvedValue({
      messages: [
        makeRawMsg('msg-1', '我想查看 12 月的订单数据'),
        makeRawMsg('msg-2', '订单总额是多少'),
      ],
      total: 2,
      query: '订单',
    });

    render(
      <SearchPanel
        isOpen={true}
        onClose={vi.fn()}
        conversationId="conv-1"
        onJumpToMessage={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText('搜索关键词'), {
      target: { value: '订单' },
    });

    await act(async () => {
      vi.advanceTimersByTime(300);
      await Promise.resolve();
      await Promise.resolve();
    });

    // 两条结果都渲染
    expect(screen.getByText(/12 月的/)).toBeInTheDocument();
    expect(screen.getByText(/总额是多少/)).toBeInTheDocument();

    // 关键词被 mark 标签包裹（多次匹配都高亮）
    const marks = document.querySelectorAll('mark');
    expect(marks.length).toBeGreaterThanOrEqual(2);
    marks.forEach((mark) => {
      expect(mark.textContent).toBe('订单');
    });
  });
});

describe('SearchPanel — 跳转和关闭', () => {
  it('点击搜索结果触发 onJumpToMessage + 关闭面板', async () => {
    mockSearchMessages.mockResolvedValue({
      messages: [makeRawMsg('msg-target', '订单数据')],
      total: 1,
      query: '订单',
    });

    const onClose = vi.fn();
    const onJumpToMessage = vi.fn();
    render(
      <SearchPanel
        isOpen={true}
        onClose={onClose}
        conversationId="conv-1"
        onJumpToMessage={onJumpToMessage}
      />,
    );
    fireEvent.change(screen.getByLabelText('搜索关键词'), {
      target: { value: '订单' },
    });

    await act(async () => {
      vi.advanceTimersByTime(300);
      await Promise.resolve();
      await Promise.resolve();
    });

    // 点击结果列表的 button
    // 注意："订单数据" 因为 mark 标签会被拆成多个 node，
    // getByText(/订单数据/) 匹配不到。改用 closest button + 唯一性断言
    const dataSpan = screen.getByText('数据');
    const resultButton = dataSpan.closest('button');
    expect(resultButton).toBeTruthy();
    fireEvent.click(resultButton!);

    expect(onJumpToMessage).toHaveBeenCalledWith('msg-target');
    expect(onClose).toHaveBeenCalled();
  });

  it('ESC 键关闭面板', () => {
    const onClose = vi.fn();
    render(
      <SearchPanel
        isOpen={true}
        onClose={onClose}
        conversationId="conv-1"
        onJumpToMessage={vi.fn()}
      />,
    );

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('点击 X 关闭按钮触发 onClose', () => {
    const onClose = vi.fn();
    render(
      <SearchPanel
        isOpen={true}
        onClose={onClose}
        conversationId="conv-1"
        onJumpToMessage={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByLabelText('关闭搜索'));
    expect(onClose).toHaveBeenCalled();
  });
});

describe('SearchPanel — 取消旧请求（AbortController）', () => {
  it('面板关闭时取消未完成的请求', async () => {
    let capturedSignal: AbortSignal | undefined;
    mockSearchMessages.mockImplementation(
      async (_convId, _q, _limit, signal) => {
        capturedSignal = signal;
        return { messages: [], total: 0, query: '' };
      },
    );

    const { rerender } = render(
      <SearchPanel
        isOpen={true}
        onClose={vi.fn()}
        conversationId="conv-1"
        onJumpToMessage={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText('搜索关键词'), {
      target: { value: 'x' },
    });

    await act(async () => {
      vi.advanceTimersByTime(300);
      await Promise.resolve();
    });

    expect(capturedSignal).toBeDefined();

    // 关闭面板
    rerender(
      <SearchPanel
        isOpen={false}
        onClose={vi.fn()}
        conversationId="conv-1"
        onJumpToMessage={vi.fn()}
      />,
    );

    // 关闭后 signal 已被 abort
    expect(capturedSignal!.aborted).toBe(true);
  });
});

describe('SearchPanel — HighlightedText 大小写不敏感', () => {
  it('英文关键词大小写不敏感高亮', async () => {
    mockSearchMessages.mockResolvedValue({
      messages: [makeRawMsg('msg-1', 'Order list and ORDER details')],
      total: 1,
      query: 'order',
    });

    render(
      <SearchPanel
        isOpen={true}
        onClose={vi.fn()}
        conversationId="conv-1"
        onJumpToMessage={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText('搜索关键词'), {
      target: { value: 'order' },
    });

    await act(async () => {
      vi.advanceTimersByTime(300);
      await Promise.resolve();
      await Promise.resolve();
    });

    // 两个 mark：原文是 "Order" 和 "ORDER"，都被高亮（保留原文大小写）
    const marks = document.querySelectorAll('mark');
    const markTexts = Array.from(marks).map((m) => m.textContent);
    expect(markTexts).toContain('Order');
    expect(markTexts).toContain('ORDER');
  });
});
