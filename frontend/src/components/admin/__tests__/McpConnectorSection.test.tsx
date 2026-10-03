import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import McpConnectorSection from '../McpConnectorSection';

const api = vi.hoisted(() => ({
  getOrgMcpConnectorStatus: vi.fn(),
  getOrgMcpCredentialStatus: vi.fn(),
  revokeOrgMcpCredential: vi.fn(),
  setOrgMcpConnectorEnabled: vi.fn(),
  setOrgMcpCredential: vi.fn(),
  testOrgMcpConnector: vi.fn(),
}));

vi.mock('../../../services/org', () => api);

const connectorState = (overrides = {}) => ({
  org_id: 'org-test', connector_id: 'test-readonly' as const,
  enabled: false, state: 'disabled', health_status: 'unknown',
  last_checked_at: null, last_error_code: null, ...overrides,
});

beforeEach(() => {
  vi.clearAllMocks();
  api.getOrgMcpConnectorStatus.mockResolvedValue({ success: true, data: connectorState() });
  api.getOrgMcpCredentialStatus.mockResolvedValue({
    success: true, data: { configured: false, version: 0 },
  });
  api.setOrgMcpCredential.mockResolvedValue({
    success: true, data: { configured: true, version: 1 },
  });
  api.testOrgMcpConnector.mockResolvedValue({
    success: true, data: connectorState({ health_status: 'ready', state: 'ready' }),
  });
  api.setOrgMcpConnectorEnabled.mockImplementation(async (_orgId: string, enabled: boolean) => ({
    success: true, data: connectorState({ enabled, state: enabled ? 'configured' : 'disabled' }),
  }));
  api.revokeOrgMcpCredential.mockResolvedValue({
    success: true, data: { configured: false, version: 2 },
  });
});

describe('MCP Connector settings', () => {
  it('runs the synthetic-only admin flow without returning or retaining the credential', async () => {
    const user = userEvent.setup();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<McpConnectorSection orgId="org-test" />);

    expect(await screen.findByText('test.sample.read')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '启用 Connector' })).toBeDisabled();
    expect(screen.queryByLabelText(/URL|地址/)).not.toBeInTheDocument();

    const credential = screen.getByLabelText('合成测试凭证');
    await user.type(credential, 'synthetic-only-secret');
    await user.click(screen.getByRole('button', { name: '安全保存' }));
    await waitFor(() => expect(api.setOrgMcpCredential).toHaveBeenCalledWith('org-test', 'synthetic-only-secret'));
    expect(credential).toHaveValue('');
    expect(screen.queryByText('synthetic-only-secret')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '测试连接' }));
    expect(await screen.findByText('连接测试通过：服务健康，工具清单与平台审核配置一致。')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '启用 Connector' }));
    expect(await screen.findByText('Connector 已为本组织启用。')).toBeInTheDocument();
    expect(api.setOrgMcpConnectorEnabled).toHaveBeenCalledWith('org-test', true);

    await user.click(screen.getByRole('button', { name: '停用 Connector' }));
    expect(await screen.findByText('Connector 已停用。')).toBeInTheDocument();
    expect(api.setOrgMcpConnectorEnabled).toHaveBeenLastCalledWith('org-test', false);

    await user.click(screen.getByRole('button', { name: '撤销凭证' }));
    expect(api.revokeOrgMcpCredential).toHaveBeenCalledWith('org-test', 1);
    expect(await screen.findByText('测试凭证已撤销。')).toBeInTheDocument();
  });

  it('reports a reviewed safe error code when connection discovery fails', async () => {
    api.testOrgMcpConnector.mockResolvedValue({
      success: false,
      data: connectorState({ health_status: 'error', last_error_code: 'MCP_SCHEMA_NOT_REVIEWED' }),
    });
    api.getOrgMcpCredentialStatus.mockResolvedValue({
      success: true, data: { configured: true, version: 4 },
    });

    render(<McpConnectorSection orgId="org-test" />);
    await screen.findByText('test.sample.read');
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('MCP_SCHEMA_NOT_REVIEWED');
    expect(screen.getByText('连接异常')).toBeInTheDocument();
  });
});
