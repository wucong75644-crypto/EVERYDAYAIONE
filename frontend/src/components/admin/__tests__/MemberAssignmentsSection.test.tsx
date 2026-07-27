import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemberAssignmentsSection } from '../MemberAssignmentsSection';
import { orgMemberAssignmentService } from '../../../services/orgMemberAssignment';
import { orgMembersService } from '../../../services/orgMembers';
import { changeMemberRole } from '../../../services/org';

vi.mock('../../../services/orgMemberAssignment');
vi.mock('../../../services/orgMembers');
vi.mock('../../../services/org', () => ({
  changeMemberRole: vi.fn(),
  createInvitation: vi.fn(),
}));
vi.mock('../../../stores/useAuthStore', () => ({
  useAuthStore: (selector: (state: unknown) => unknown) => selector({
    currentOrg: { role: 'owner' },
  }),
}));

const member = {
  user_id: 'user-a',
  nickname: '张三',
  avatar_url: null,
  phone: '138****0000',
  org_role: 'member' as const,
  assignment: {
    department_id: 'dept-a',
    department_name: '运营部',
    department_type: 'ops' as const,
    position_id: 'position-a',
    position_code: 'member' as const,
    position_name: '员工',
    job_title: null,
    data_scope: 'self' as const,
    data_scope_dept_ids: [],
  },
};

describe('MemberAssignmentsSection 统一成员列表', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(orgMemberAssignmentService.listMembers).mockResolvedValue([member]);
    vi.mocked(orgMemberAssignmentService.listDepartments).mockResolvedValue([
      { id: 'dept-a', name: '运营部', type: 'ops', sort_order: 1 },
    ]);
    vi.mocked(orgMemberAssignmentService.listPositions).mockResolvedValue([
      { id: 'position-a', code: 'member', name: '员工', level: 50 },
    ]);
    vi.mocked(orgMembersService.listWecomCollected).mockResolvedValue([
      {
        user_id: 'user-a',
        nickname: '张三',
        avatar_url: null,
        wecom_userid: 'wecom-a',
        wecom_nickname: '张三',
        channel: 'smart_robot',
        last_chat_type: 'single',
        joined_at: null,
        assignment: null,
      },
    ]);
    vi.mocked(changeMemberRole).mockResolvedValue({ success: true });
  });

  it('用正式成员列表展示任职、角色和企微状态', async () => {
    render(<MemberAssignmentsSection orgId="org-a" />);

    expect(await screen.findByText('张三')).toBeInTheDocument();
    expect(screen.getByText('运营部')).toBeInTheDocument();
    expect(screen.getByText('成员')).toBeInTheDocument();
    expect(screen.getAllByText('已关联企微')).toHaveLength(2);
  });

  it('企微状态加载失败时保留正式成员列表', async () => {
    vi.mocked(orgMembersService.listWecomCollected).mockRejectedValueOnce(
      new Error('network error'),
    );

    render(<MemberAssignmentsSection orgId="org-a" />);

    expect(await screen.findByText('张三')).toBeInTheDocument();
    expect(screen.getByText(/企微关联状态暂不可用/)).toBeInTheDocument();
    expect(screen.getByText('企微状态未知')).toBeInTheDocument();
  });

  it('企微状态仍在加载时先展示正式成员', async () => {
    vi.mocked(orgMembersService.listWecomCollected).mockReturnValueOnce(
      new Promise(() => {}),
    );

    render(<MemberAssignmentsSection orgId="org-a" />);

    expect(await screen.findByText('张三')).toBeInTheDocument();
    expect(screen.getByText('企微状态未知')).toBeInTheDocument();
  });

  it('正式成员为空时显示企业空状态', async () => {
    vi.mocked(orgMemberAssignmentService.listMembers).mockResolvedValueOnce([]);
    vi.mocked(orgMembersService.listWecomCollected).mockResolvedValueOnce([]);

    render(<MemberAssignmentsSection orgId="org-a" />);

    expect(await screen.findByText('企业暂无有效成员')).toBeInTheDocument();
  });

  it('企业 owner 可以在成员编辑态修改角色', async () => {
    const user = userEvent.setup();
    render(<MemberAssignmentsSection orgId="org-a" />);

    await screen.findByText('张三');
    await user.click(screen.getByTitle('编辑任职'));
    await user.selectOptions(screen.getByLabelText('企业角色'), 'admin');
    await user.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => {
      expect(changeMemberRole).toHaveBeenCalledWith('org-a', 'user-a', 'admin');
    });
  });
});
