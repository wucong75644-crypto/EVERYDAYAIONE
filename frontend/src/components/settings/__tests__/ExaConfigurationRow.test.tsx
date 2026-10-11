import { render, screen, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import ExaConfigurationRow, { ExaConfigurationForm } from '../ExaConfigurationRow';
import * as service from '../../../services/agentReach';
vi.mock('../../../services/agentReach', () => ({ getExaConfiguration: vi.fn(), saveExaConfiguration: vi.fn() }));
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(service.getExaConfiguration).mockResolvedValue({ configured: true, source: 'server', verified_at: null });
  vi.mocked(service.saveExaConfiguration).mockResolvedValue({ configured: true, source: 'organization', verified_at: 'now' });
});
afterEach(cleanup);
it('selects inline configuration without opening a dialog', async () => {
  const user = userEvent.setup(); const configure = vi.fn();
  render(<table><tbody><ExaConfigurationRow admin onConfigure={configure} /></tbody></table>);
  await screen.findByText('服务器默认 API Key');
  await user.click(screen.getByRole('button', { name: '配置 API Key' }));
  expect(configure).toHaveBeenCalledOnce();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
it('saves and clears the inline password field while retaining confirmation', async () => {
  const user = userEvent.setup(); const saved = vi.fn();
  render(<ExaConfigurationForm onSaved={saved} onClose={vi.fn()} />);
  expect(screen.getByLabelText('Exa API Key')).toHaveAttribute('type', 'password');
  expect(screen.getByLabelText('Exa API Key')).toHaveValue('');
  await user.type(screen.getByLabelText('Exa API Key'), 'test-only-exa-key');
  await user.click(screen.getByRole('button', { name: '验证并保存' }));
  expect(await screen.findByRole('status')).toHaveTextContent('已验证并保存');
  expect(service.saveExaConfiguration).toHaveBeenCalledWith('test-only-exa-key');
  expect(saved).toHaveBeenCalledOnce();
  expect(screen.getByLabelText('Exa API Key')).toHaveValue('');
});
it('does not announce success when verification fails', async () => {
  vi.mocked(service.saveExaConfiguration).mockRejectedValue(new Error('Exa 密钥无效'));
  const user = userEvent.setup(); const saved = vi.fn(); const close = vi.fn();
  render(<ExaConfigurationForm onSaved={saved} onClose={close} />);
  await user.type(screen.getByLabelText('Exa API Key'), 'invalid');
  await user.click(screen.getByRole('button', { name: '验证并保存' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Exa 密钥无效');
  expect(saved).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: '取消配置' }));
  expect(close).toHaveBeenCalledOnce();
});
it('does not expose key configuration controls to members', () => {
  render(<table><tbody><ExaConfigurationRow admin={false} onConfigure={vi.fn()} /></tbody></table>);
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  expect(service.getExaConfiguration).not.toHaveBeenCalled();
});
