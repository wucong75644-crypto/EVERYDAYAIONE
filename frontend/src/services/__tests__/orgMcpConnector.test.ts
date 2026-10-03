import { beforeEach, expect, it, vi } from 'vitest';
import { request } from '../api';
import {
  getOrgMcpConnectorStatus,
  getOrgMcpCredentialStatus,
  revokeOrgMcpCredential,
  setOrgMcpConnectorEnabled,
  setOrgMcpCredential,
  testOrgMcpConnector,
} from '../org';

vi.mock('../api', () => ({ request: vi.fn() }));

beforeEach(() => vi.mocked(request).mockResolvedValue({ success: true, data: {} }));

it('uses only the platform-fixed test Connector routes and never requests credential material', async () => {
  await getOrgMcpConnectorStatus('org-1');
  expect(request).toHaveBeenLastCalledWith({
    method: 'GET', url: '/org/org-1/mcp-connectors/test-readonly',
  });

  await getOrgMcpCredentialStatus('org-1');
  expect(request).toHaveBeenLastCalledWith({
    method: 'GET', url: '/org/org-1/mcp-connectors/test-readonly/credential',
  });

  await setOrgMcpCredential('org-1', 'synthetic-only');
  expect(request).toHaveBeenLastCalledWith({
    method: 'PUT', url: '/org/org-1/mcp-connectors/test-readonly/credential',
    data: { token: 'synthetic-only' },
  });

  await testOrgMcpConnector('org-1');
  expect(request).toHaveBeenLastCalledWith({
    method: 'POST', url: '/org/org-1/mcp-connectors/test-readonly/test',
  });

  await setOrgMcpConnectorEnabled('org-1', true);
  expect(request).toHaveBeenLastCalledWith({
    method: 'PUT', url: '/org/org-1/mcp-connectors/test-readonly', data: { enabled: true },
  });

  await revokeOrgMcpCredential('org-1', 7);
  expect(request).toHaveBeenLastCalledWith({
    method: 'DELETE', url: '/org/org-1/mcp-connectors/test-readonly/credential',
    params: { expected_version: 7 },
  });
});
