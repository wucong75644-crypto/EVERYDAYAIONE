import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import McpConnectorSection from '../McpConnectorSection';

const api = vi.hoisted(() => ({
  getOrgMcpConnectorStatus: vi.fn(),
  setOrgMcpConnectorEnabled: vi.fn(),
  setupOrgMcpConnector: vi.fn(),
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
  api.setupOrgMcpConnector.mockResolvedValue({
    success: true, data: connectorState({
      enabled: true, state: 'ready', health_status: 'ready',
    }),
  });
  api.setOrgMcpConnectorEnabled.mockResolvedValue({
    success: true, data: connectorState({ enabled: false, state: 'disabled' }),
  });
});

describe('MCP Connector settings', () => {
  it('connects the reviewed synthetic MCP without asking for a URL or credential', async () => {
    const user = userEvent.setup();
    render(<McpConnectorSection orgId="org-test" />);

    expect(await screen.findByText('test.sample.read')).toBeInTheDocument();
    expect(screen.getByText(/不需要找 MCP 链接或填写 Token/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/URL|地址|Token|凭证/)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '一键连接并启用' }));
    expect(await screen.findByRole('status')).toHaveTextContent('已连接并启用');
    expect(api.setupOrgMcpConnector).toHaveBeenCalledWith('org-test');

    await user.click(screen.getByRole('button', { name: '停用' }));
    expect(await screen.findByRole('status')).toHaveTextContent('测试 Connector 已停用');
    expect(api.setOrgMcpConnectorEnabled).toHaveBeenCalledWith('org-test', false);
  });

  it('shows only the reviewed MCP error code when setup fails', async () => {
    api.setupOrgMcpConnector.mockResolvedValue({
      success: false,
      data: connectorState({ health_status: 'error', last_error_code: 'MCP_SCHEMA_NOT_REVIEWED' }),
    });

    render(<McpConnectorSection orgId="org-test" />);
    await screen.findByText('test.sample.read');
    fireEvent.click(screen.getByRole('button', { name: '一键连接并启用' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('MCP_SCHEMA_NOT_REVIEWED');
    expect(screen.getByText('连接异常')).toBeInTheDocument();
  });
});
