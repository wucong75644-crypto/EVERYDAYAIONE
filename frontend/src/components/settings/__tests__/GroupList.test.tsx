/**
 * GroupList 测试 — 渲染 / 加载 / 空 / 错误 / 未命名群
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import GroupList from '../GroupList';
import { wecomChatTargetsService } from '../../../services/wecomChatTargets';
import type { WecomGroup } from '../../../types/wecomChatTargets';

vi.mock('../../../services/wecomChatTargets');

const mockGroup: WecomGroup = {
  id: 'g1',
  chatid: 'wriNwWOAAATq-Xq5_grMtJe8rP7SmR7A',
  chat_type: 'group',
  chat_name: '运营群',
  last_active: '2026-04-11T20:00:00Z',
  first_seen: '2026-04-01T10:00:00Z',
  message_count: 42,
  is_active: true,
};

describe('GroupList', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('加载中显示 spinner', () => {
    vi.mocked(wecomChatTargetsService.listGroups).mockReturnValue(
      new Promise(() => {}),
    );
    render(<GroupList />);
    expect(screen.getByText('加载中...')).toBeInTheDocument();
  });

  it('成功时渲染群列表', async () => {
    vi.mocked(wecomChatTargetsService.listGroups).mockResolvedValue([mockGroup]);
    render(<GroupList />);

    await waitFor(() => {
      expect(screen.getByText('运营群')).toBeInTheDocument();
    });
    expect(screen.getByText('42')).toBeInTheDocument();
  });

  it('未命名群显示 "未命名"', async () => {
    vi.mocked(wecomChatTargetsService.listGroups).mockResolvedValue([
      { ...mockGroup, chat_name: null },
    ]);
    render(<GroupList />);

    await waitFor(() => {
      expect(screen.getByText('未命名')).toBeInTheDocument();
    });
  });

  it('空数据时显示提示', async () => {
    vi.mocked(wecomChatTargetsService.listGroups).mockResolvedValue([]);
    render(<GroupList />);

    await waitFor(() => {
      expect(screen.getByText(/还没有收集到任何群聊/)).toBeInTheDocument();
    });
  });

  it('错误时显示重试按钮', async () => {
    vi.mocked(wecomChatTargetsService.listGroups).mockRejectedValue(
      new Error('network error'),
    );
    render(<GroupList />);

    await waitFor(() => {
      expect(screen.getByText('加载失败，请重试')).toBeInTheDocument();
    });
  });
});
