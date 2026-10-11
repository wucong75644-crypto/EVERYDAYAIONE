import { render, screen, within, waitFor, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import ExaConfigurationRow from '../ExaConfigurationRow';
import * as service from '../../../services/agentReach';
vi.mock('../../../services/agentReach', () => ({ getExaConfiguration: vi.fn(), saveExaConfiguration: vi.fn() }));
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(service.getExaConfiguration).mockResolvedValue({ configured: true, source: 'server', verified_at: null });
  vi.mocked(service.saveExaConfiguration).mockResolvedValue({ configured: true, source: 'organization', verified_at: 'now' });
});
afterEach(cleanup);
const renderRow = (admin = true) => render(<table><tbody><ExaConfigurationRow admin={admin} /></tbody></table>);
it('opens a password dialog, saves and clears the key without echoing the previous secret', async () => {
  const user = userEvent.setup(); renderRow();
  await screen.findByText('服务器默认 API Key');
  await user.click(screen.getByRole('button', { name: '配置 API Key' }));
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByLabelText('Exa API Key')).toHaveAttribute('type', 'password');
  expect(within(dialog).getByLabelText('Exa API Key')).toHaveValue('');
  await user.type(within(dialog).getByLabelText('Exa API Key'), 'test-only-exa-key');
  await user.click(within(dialog).getByRole('button', { name: '验证并保存' }));
  await waitFor(() => expect(service.saveExaConfiguration).toHaveBeenCalledWith('test-only-exa-key'));
  await screen.findByText('本组织 API Key');
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  await user.click(screen.getByRole('button', { name: '配置 API Key' }));
  expect(screen.getByLabelText('Exa API Key')).toHaveValue('');
});
it('keeps existing state on failed verification and clears input on cancel', async () => {
  vi.mocked(service.saveExaConfiguration).mockRejectedValue(new Error('Exa 密钥无效'));
  const user = userEvent.setup(); renderRow();
  await screen.findByText('服务器默认 API Key');
  await user.click(screen.getByRole('button', { name: '配置 API Key' }));
  await user.type(screen.getByLabelText('Exa API Key'), 'invalid');
  await user.click(screen.getByRole('button', { name: '验证并保存' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Exa 密钥无效');
  expect(screen.getByText('服务器默认 API Key')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '取消' }));
  await user.click(screen.getByRole('button', { name: '配置 API Key' }));
  expect(screen.getByLabelText('Exa API Key')).toHaveValue('');
});
it('does not expose key configuration controls to members', () => {
  renderRow(false);
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  expect(service.getExaConfiguration).not.toHaveBeenCalled();
});
