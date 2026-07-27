import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import OrgManagePanel from '../OrgManagePanel';
import {
  getWecomStatus,
  listOrgConfigs,
  type OrgConfigStatus,
} from '../../../services/org';

vi.mock('../../../services/org', () => ({
  createInvitation: vi.fn(),
  getWecomStatus: vi.fn(),
  listMembers: vi.fn(),
  listOrgConfigs: vi.fn(),
  setOrgConfig: vi.fn(),
  testErpConnection: vi.fn(),
  testWecomConnection: vi.fn(),
}));

const mockGetWecomStatus = vi.mocked(getWecomStatus);
const mockListOrgConfigs = vi.mocked(listOrgConfigs);

function configStatus(configKey: string, version: number): OrgConfigStatus {
  return {
    config_key: configKey,
    configured: true,
    source: 'organization',
    updated_at: null,
    version,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockListOrgConfigs.mockResolvedValue({
    success: true,
    data: [
      configStatus('erp.app_credentials', 2),
      configStatus('erp.token_pair', 4),
    ],
  });
  mockGetWecomStatus.mockResolvedValue({
    success: true,
    data: {
      'wecom.bot_credentials': {
        configured: true,
        source: 'organization',
        version: 7,
      },
    },
  });
});

describe('企业凭证组状态交互', () => {
  it('ERP 隐藏内部版本，并从明确状态展开整组编辑', async () => {
    render(<OrgManagePanel orgId="org-a" />);

    const appCredentials = await screen.findByRole('heading', { name: '应用凭证' });
    const appSection = appCredentials.closest('section');
    expect(appSection).not.toBeNull();
    expect(within(appSection!).getByText('已配置')).toBeInTheDocument();
    expect(screen.queryByText(/v2|v4/)).not.toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: '重新配置' })).toHaveLength(2);
    expect(screen.getByRole('button', { name: '测试 ERP 连接' })).toBeInTheDocument();

    await userEvent.click(within(appSection!).getByRole('button', { name: '重新配置' }));

    expect(within(appSection!).getByText('App Key')).toBeInTheDocument();
    expect(within(appSection!).getByText('App Secret')).toBeInTheDocument();
    expect(within(appSection!).getByRole('button', { name: '保存凭证' })).toBeInTheDocument();
    expect(within(appSection!).getByRole('button', { name: '取消' })).toBeInTheDocument();

    await userEvent.click(within(appSection!).getByRole('button', { name: '取消' }));
    expect(within(appSection!).queryByText('App Key')).not.toBeInTheDocument();
  });

  it('企业微信机器人使用相同状态与重新配置入口', async () => {
    render(<OrgManagePanel orgId="org-a" />);
    await screen.findByRole('heading', { name: '应用凭证' });

    await userEvent.click(screen.getByRole('button', { name: '企业微信' }));

    const heading = await screen.findByRole('heading', { name: '机器人凭证' });
    const botSection = heading.closest('section');
    expect(botSection).not.toBeNull();
    expect(within(botSection!).getByText('已配置')).toBeInTheDocument();
    expect(screen.queryByText(/完整凭证组保存|v7/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '测试企微连接' })).toBeInTheDocument();

    await userEvent.click(within(botSection!).getByRole('button', { name: '重新配置' }));

    expect(within(botSection!).getByLabelText('Bot ID')).toBeInTheDocument();
    expect(within(botSection!).getByLabelText('Bot Secret')).toBeInTheDocument();
    expect(within(botSection!).getByRole('button', { name: '保存凭证' })).toBeInTheDocument();
    expect(within(botSection!).getByRole('button', { name: '取消' })).toBeInTheDocument();
    await waitFor(() => expect(mockGetWecomStatus).toHaveBeenCalledWith('org-a'));
  });

  it('未配置时直接展示字段，并对不完整凭证给出反馈', async () => {
    mockListOrgConfigs.mockResolvedValueOnce({ success: true, data: [] });
    render(<OrgManagePanel orgId="org-a" />);

    const appCredentials = await screen.findByRole('heading', { name: '应用凭证' });
    const appSection = appCredentials.closest('section');
    expect(appSection).not.toBeNull();
    expect(within(appSection!).getByText('未配置')).toBeInTheDocument();
    expect(within(appSection!).queryByRole('button', { name: '重新配置' })).not.toBeInTheDocument();

    await userEvent.click(within(appSection!).getByRole('button', { name: '保存凭证' }));

    expect(screen.getByText('应用凭证必须完整填写后一次保存')).toBeInTheDocument();
  });
});
