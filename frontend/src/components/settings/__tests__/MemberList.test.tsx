/**
 * MemberList 测试 — 渲染 / 加载 / 空状态 / 错误
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import MemberList from '../MemberList';
import { orgMembersService } from '../../../services/orgMembers';
import type { WecomCollectedMember } from '../../../types/orgMembers';

vi.mock('../../../services/orgMembers');

const mockMember: WecomCollectedMember = {
  user_id: 'u1',
  nickname: '张三',
  avatar_url: null,
  wecom_userid: 'ww_zhang',
  wecom_nickname: '张三',
  channel: 'smart_robot',
  last_chat_type: 'single',
  joined_at: '2026-04-10T10:00:00Z',
  assignment: {
    department_id: 'd1',
    department_name: '运营一部',
    department_type: 'ops',
    position_id: 'p1',
    position_code: 'member',
    position_name: '员工',
    job_title: null,
    data_scope: 'self',
    data_scope_dept_ids: [],
  },
};

describe('MemberList', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('加载中显示 spinner', () => {
    vi.mocked(orgMembersService.listWecomCollected).mockReturnValue(
      new Promise(() => {}),
    );
    vi.mocked(orgMembersService.listDepartments).mockResolvedValue([]);
    vi.mocked(orgMembersService.listPositions).mockResolvedValue([]);

    render(<MemberList />);
    expect(screen.getByText('加载中...')).toBeInTheDocument();
  });

  it('成功时渲染员工列表', async () => {
    vi.mocked(orgMembersService.listWecomCollected).mockResolvedValue([mockMember]);
    vi.mocked(orgMembersService.listDepartments).mockResolvedValue([]);
    vi.mocked(orgMembersService.listPositions).mockResolvedValue([]);

    render(<MemberList />);

    await waitFor(() => {
      expect(screen.getByText('张三')).toBeInTheDocument();
    });
    expect(screen.getByText('运营一部')).toBeInTheDocument();
    expect(screen.getByText('员工')).toBeInTheDocument();
    expect(screen.getByText('仅自己')).toBeInTheDocument();
  });

  it('空数据时显示提示', async () => {
    vi.mocked(orgMembersService.listWecomCollected).mockResolvedValue([]);
    vi.mocked(orgMembersService.listDepartments).mockResolvedValue([]);
    vi.mocked(orgMembersService.listPositions).mockResolvedValue([]);

    render(<MemberList />);

    await waitFor(() => {
      expect(screen.getByText(/还没有员工和机器人聊过天/)).toBeInTheDocument();
    });
  });

  it('错误时显示重试按钮', async () => {
    vi.mocked(orgMembersService.listWecomCollected).mockRejectedValue(
      new Error('network error'),
    );
    vi.mocked(orgMembersService.listDepartments).mockResolvedValue([]);
    vi.mocked(orgMembersService.listPositions).mockResolvedValue([]);

    render(<MemberList />);

    await waitFor(() => {
      expect(screen.getByText('加载失败，请重试')).toBeInTheDocument();
      expect(screen.getByText('重试')).toBeInTheDocument();
    });
  });

  it('未分配部门时显示 "未分配"', async () => {
    vi.mocked(orgMembersService.listWecomCollected).mockResolvedValue([
      { ...mockMember, assignment: null },
    ]);
    vi.mocked(orgMembersService.listDepartments).mockResolvedValue([]);
    vi.mocked(orgMembersService.listPositions).mockResolvedValue([]);

    render(<MemberList />);

    await waitFor(() => {
      expect(screen.getByText('张三')).toBeInTheDocument();
    });
    expect(screen.getAllByText('未分配').length).toBeGreaterThanOrEqual(2);
  });
});
