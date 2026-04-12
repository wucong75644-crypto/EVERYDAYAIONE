/**
 * ErrorMonitorPanel 单元测试
 *
 * 覆盖：初始加载 / 空列表 / 加载失败 / 筛选 / 标记处理 /
 *       AI 分析 / 清除流程 / 清除取消 / 展开详情 / 分页
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ErrorMonitorPanel from '../ErrorMonitorPanel';

// ── Mock 依赖 ──────────────────────────────────────────

vi.mock('../../../services/errorMonitor');
vi.mock('framer-motion', async () => {
  const actual = await vi.importActual<typeof import('framer-motion')>('framer-motion');
  return {
    ...actual,
    AnimatePresence: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    m: {
      div: ({ children, variants, initial, animate, exit, transition, ...rest }: Record<string, unknown>) => (
        <div {...rest}>{children as React.ReactNode}</div>
      ),
      span: ({ children, ...rest }: Record<string, unknown>) => <span {...rest}>{children as React.ReactNode}</span>,
      button: ({ children, ...rest }: Record<string, unknown>) => <button {...rest}>{children as React.ReactNode}</button>,
    },
  };
});

import {
  listErrors,
  getErrorStats,
  summarizeErrors,
  resolveError,
  clearErrors,
  type ErrorLogItem,
  type ErrorStatsResponse,
} from '../../../services/errorMonitor';

const mockListErrors = vi.mocked(listErrors);
const mockGetErrorStats = vi.mocked(getErrorStats);
const mockSummarizeErrors = vi.mocked(summarizeErrors);
const mockResolveError = vi.mocked(resolveError);
const mockClearErrors = vi.mocked(clearErrors);

// ── 测试数据 ──────────────────────────────────────────

const mockStats: ErrorStatsResponse = {
  today_total: 5,
  today_critical: 2,
  week_total: 30,
  unresolved: 8,
  top_modules: [{ module: 'erp_agent', count: 10 }],
};

function makeErrorItem(overrides: Partial<ErrorLogItem> = {}): ErrorLogItem {
  return {
    id: 1,
    fingerprint: 'abc123',
    level: 'ERROR',
    module: 'erp_agent',
    function: 'handle_request',
    line: 42,
    message: 'Connection timeout',
    traceback: 'Traceback (most recent call last):\n  File "test.py", line 42',
    occurrence_count: 3,
    first_seen_at: '2026-04-10T08:00:00Z',
    last_seen_at: '2026-04-12T10:00:00Z',
    org_id: null,
    is_critical: false,
    is_resolved: false,
    resolved_at: null,
    resolved_by: null,
    ...overrides,
  };
}

// ── Setup ──────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
});

function setupDefaultMocks(items: ErrorLogItem[] = [makeErrorItem()], total?: number) {
  mockListErrors.mockResolvedValue({
    items,
    total: total ?? items.length,
    page: 1,
    page_size: 20,
  });
  mockGetErrorStats.mockResolvedValue(mockStats);
}

// ── 测试 ──────────────────────────────────────────────

describe('ErrorMonitorPanel', () => {
  // ── 初始加载 ──────────────────────────────────────────

  it('加载中显示 spinner', () => {
    mockListErrors.mockReturnValue(new Promise(() => {}));
    mockGetErrorStats.mockReturnValue(new Promise(() => {}));

    render(<ErrorMonitorPanel />);
    expect(screen.getByText('加载中...')).toBeInTheDocument();
  });

  it('成功加载后显示统计卡片和错误列表', async () => {
    setupDefaultMocks();

    render(<ErrorMonitorPanel />);

    await waitFor(() => {
      expect(screen.getByText('5')).toBeInTheDocument(); // today_total
    });
    expect(screen.getByText('今日错误')).toBeInTheDocument();
    expect(screen.getByText('今日致命')).toBeInTheDocument();
    expect(screen.getByText('本周总计')).toBeInTheDocument();
    expect(screen.getAllByText('未处理').length).toBeGreaterThanOrEqual(1); // stat card + select option
    expect(screen.getByText('Connection timeout')).toBeInTheDocument();
  });

  it('空列表显示提示文字', async () => {
    setupDefaultMocks([]);

    render(<ErrorMonitorPanel />);

    await waitFor(() => {
      expect(screen.getByText('没有错误记录')).toBeInTheDocument();
    });
  });

  it('加载失败显示错误提示', async () => {
    mockListErrors.mockRejectedValue(new Error('Network error'));
    mockGetErrorStats.mockRejectedValue(new Error('Network error'));

    render(<ErrorMonitorPanel />);

    await waitFor(() => {
      expect(screen.getByText('加载错误日志失败')).toBeInTheDocument();
    });
  });

  // ── 筛选 ──────────────────────────────────────────────

  it('切换级别筛选后重新加载', async () => {
    const user = userEvent.setup();
    setupDefaultMocks();

    render(<ErrorMonitorPanel />);
    await waitFor(() => {
      expect(screen.getByText('Connection timeout')).toBeInTheDocument();
    });

    const levelSelect = screen.getAllByRole('combobox')[0];
    await user.selectOptions(levelSelect, 'CRITICAL');

    await waitFor(() => {
      // 初始加载 1 次 + 筛选 1 次
      expect(mockListErrors).toHaveBeenCalledTimes(2);
      expect(mockListErrors).toHaveBeenLastCalledWith(
        expect.objectContaining({ level: 'CRITICAL' }),
      );
    });
  });

  // ── 标记处理 ──────────────────────────────────────────

  it('点击处理按钮调用 resolveError 并更新行状态', async () => {
    const user = userEvent.setup();
    setupDefaultMocks();
    mockResolveError.mockResolvedValue({ success: true });

    render(<ErrorMonitorPanel />);
    await waitFor(() => {
      expect(screen.getByText('Connection timeout')).toBeInTheDocument();
    });

    // "处理" 按钮（排除"清除已处理"按钮）
    const resolveBtns = screen.getAllByRole('button', { name: /处理/ });
    const resolveBtn = resolveBtns.find(btn => btn.textContent?.trim() === '处理')!;
    await user.click(resolveBtn);

    await waitFor(() => {
      expect(mockResolveError).toHaveBeenCalledWith(1);
    });
    // "已处理" 出现在行内状态标签和 select option 中
    expect(screen.getAllByText('已处理').length).toBeGreaterThanOrEqual(2);
  });

  // ── AI 分析 ──────────────────────────────────────────

  it('点击 AI 分析按钮显示分析结果', async () => {
    const user = userEvent.setup();
    setupDefaultMocks();
    mockSummarizeErrors.mockResolvedValue({ summary: '本周主要错误集中在 ERP 模块' });

    render(<ErrorMonitorPanel />);
    await waitFor(() => {
      expect(screen.getByText('Connection timeout')).toBeInTheDocument();
    });

    const aiBtn = screen.getByRole('button', { name: /AI 分析/ });
    await user.click(aiBtn);

    await waitFor(() => {
      expect(screen.getByText('本周主要错误集中在 ERP 模块')).toBeInTheDocument();
    });
    expect(screen.getByText('AI 分析报告')).toBeInTheDocument();
  });

  // ── 清除流程 ──────────────────────────────────────────

  it('清除流程：弹窗确认 → 调用 API → 显示成功提示', async () => {
    const user = userEvent.setup();
    setupDefaultMocks();
    mockClearErrors.mockResolvedValue({ success: true, deleted: 5 });

    render(<ErrorMonitorPanel />);
    await waitFor(() => {
      expect(screen.getByText('Connection timeout')).toBeInTheDocument();
    });

    // 点击清除按钮
    const clearBtn = screen.getByRole('button', { name: /清除已处理/ });
    await user.click(clearBtn);

    // 弹窗出现
    expect(screen.getByText('确定清除已处理的错误日志？')).toBeInTheDocument();

    // 点击确认
    const confirmBtn = screen.getByRole('button', { name: '确认清除' });
    await user.click(confirmBtn);

    await waitFor(() => {
      expect(mockClearErrors).toHaveBeenCalledWith(true);
    });

    // 成功提示
    await waitFor(() => {
      expect(screen.getByText(/已清除 5 条记录/)).toBeInTheDocument();
    });
  });

  it('清除弹窗点击取消不调用 API', async () => {
    const user = userEvent.setup();
    setupDefaultMocks();

    render(<ErrorMonitorPanel />);
    await waitFor(() => {
      expect(screen.getByText('Connection timeout')).toBeInTheDocument();
    });

    // 打开弹窗
    await user.click(screen.getByRole('button', { name: /清除已处理/ }));
    expect(screen.getByText('确定清除已处理的错误日志？')).toBeInTheDocument();

    // 点击取消
    await user.click(screen.getByRole('button', { name: '取消' }));

    // 弹窗消失，API 未被调用
    await waitFor(() => {
      expect(screen.queryByText('确定清除已处理的错误日志？')).not.toBeInTheDocument();
    });
    expect(mockClearErrors).not.toHaveBeenCalled();
  });

  // ── 展开详情 ──────────────────────────────────────────

  it('点击行展开详情，再次点击收起', async () => {
    const user = userEvent.setup();
    const item = makeErrorItem({ org_id: 'org-123' });
    setupDefaultMocks([item]);

    render(<ErrorMonitorPanel />);
    await waitFor(() => {
      expect(screen.getByText('Connection timeout')).toBeInTheDocument();
    });

    // 详情不可见
    expect(screen.queryByText(/指纹:/)).not.toBeInTheDocument();

    // 点击展开
    await user.click(screen.getByText('Connection timeout'));
    expect(screen.getByText(/指纹: abc123/)).toBeInTheDocument();
    expect(screen.getByText(/handle_request:42/)).toBeInTheDocument();
    expect(screen.getByText(/企业: org-123/)).toBeInTheDocument();

    // 再次点击收起
    await user.click(screen.getByText('Connection timeout'));
    expect(screen.queryByText(/指纹:/)).not.toBeInTheDocument();
  });

  // ── 分页 ──────────────────────────────────────────────

  it('显示分页控件并支持翻页', async () => {
    const user = userEvent.setup();
    // 25 条数据 / 20 每页 = 2 页
    setupDefaultMocks([makeErrorItem()], 25);

    render(<ErrorMonitorPanel />);
    await waitFor(() => {
      expect(screen.getByText('共 25 条，第 1/2 页')).toBeInTheDocument();
    });

    // 上一页禁用
    const prevBtn = screen.getByRole('button', { name: '上一页' });
    expect(prevBtn).toBeDisabled();

    // 点击下一页
    const nextBtn = screen.getByRole('button', { name: '下一页' });
    await user.click(nextBtn);

    await waitFor(() => {
      expect(mockListErrors).toHaveBeenLastCalledWith(
        expect.objectContaining({ page: 2 }),
      );
    });
  });

  it('总数不超过一页时不显示分页', async () => {
    setupDefaultMocks([makeErrorItem()], 10);

    render(<ErrorMonitorPanel />);
    await waitFor(() => {
      expect(screen.getByText('Connection timeout')).toBeInTheDocument();
    });

    expect(screen.queryByText('上一页')).not.toBeInTheDocument();
  });

  // ── 错误提示关闭 ──────────────────────────────────────

  it('错误提示可手动关闭', async () => {
    const user = userEvent.setup();
    mockListErrors.mockRejectedValue(new Error('fail'));
    mockGetErrorStats.mockRejectedValue(new Error('fail'));

    render(<ErrorMonitorPanel />);
    await waitFor(() => {
      expect(screen.getByText('加载错误日志失败')).toBeInTheDocument();
    });

    // 找到错误提示区域的关闭按钮（error 区块内的 button）
    const errorContainer = screen.getByText('加载错误日志失败').closest('div')!;
    const closeBtn = within(errorContainer).getByRole('button');
    await user.click(closeBtn);

    await waitFor(() => {
      expect(screen.queryByText('加载错误日志失败')).not.toBeInTheDocument();
    });
  });
});
