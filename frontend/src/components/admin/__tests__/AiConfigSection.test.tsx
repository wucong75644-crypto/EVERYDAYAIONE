import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AiConfigSection from '../AiConfigSection';
import {
  deleteOrgConfig,
  listOrgConfigs,
  type OrgConfigStatus,
} from '../../../services/org';

vi.mock('../../../services/org', () => ({
  deleteOrgConfig: vi.fn(),
  listOrgConfigs: vi.fn(),
  setOrgConfig: vi.fn(),
}));

const mockDeleteOrgConfig = vi.mocked(deleteOrgConfig);
const mockListOrgConfigs = vi.mocked(listOrgConfigs);

function status(
  configKey: string,
  version: number,
  configured = true,
): OrgConfigStatus {
  return {
    config_key: configKey,
    configured,
    version,
    source: configured ? 'organization' : null,
    updated_at: null,
  };
}

function response(data: OrgConfigStatus[]) {
  return { success: true, data };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

async function switchToPlatform() {
  const user = userEvent.setup();
  await user.click(screen.getByRole('radio', { name: /使用平台 AI 服务/ }));
}

beforeEach(() => {
  vi.clearAllMocks();
  mockDeleteOrgConfig.mockResolvedValue({
    success: true,
    data: status('ai.google.api_key', 2, false),
  });
});

describe('AiConfigSection platform mode', () => {
  it('删除全部已配置 Key，并按各自 version 执行 CAS', async () => {
    mockListOrgConfigs
      .mockResolvedValueOnce(response([
        status('ai.google.api_key', 3),
        status('ai.kie.api_key', 7),
      ]))
      .mockResolvedValueOnce(response([]));
    render(<AiConfigSection orgId="org-a" />);
    await screen.findByRole('radio', { name: /使用自有 AI Key/ });

    await switchToPlatform();

    await waitFor(() => {
      expect(mockDeleteOrgConfig).toHaveBeenCalledTimes(2);
      expect(mockDeleteOrgConfig).toHaveBeenCalledWith(
        'org-a', 'ai.google.api_key', 3,
      );
      expect(mockDeleteOrgConfig).toHaveBeenCalledWith(
        'org-a', 'ai.kie.api_key', 7,
      );
      expect(screen.getByText('已切换到平台 AI 服务')).toBeInTheDocument();
    });
    expect(screen.getByRole('radio', { name: /使用平台 AI 服务/ })).toBeChecked();
  });

  it('没有 active 企业 Key 时不发送删除请求', async () => {
    mockListOrgConfigs.mockResolvedValue(response([]));
    render(<AiConfigSection orgId="org-a" />);
    const platform = await screen.findByRole(
      'radio',
      { name: /使用平台 AI 服务/ },
    );

    await userEvent.click(platform);

    expect(mockDeleteOrgConfig).not.toHaveBeenCalled();
    expect(mockListOrgConfigs).toHaveBeenCalledTimes(1);
  });

  it('版本冲突后重载权威状态，并在仍 active 时保持 BYOK', async () => {
    mockListOrgConfigs
      .mockResolvedValueOnce(response([status('ai.google.api_key', 2)]))
      .mockResolvedValueOnce(response([status('ai.google.api_key', 3)]));
    mockDeleteOrgConfig.mockRejectedValueOnce(new Error('conflict'));
    render(<AiConfigSection orgId="org-a" />);
    await screen.findByText(/v2/);

    await switchToPlatform();

    await screen.findByText(/未能完全切换到平台服务/);
    expect(mockListOrgConfigs).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('radio', { name: /使用自有 AI Key/ })).toBeChecked();
    expect(screen.getByText(/v3/)).toBeInTheDocument();
  });

  it('部分删除失败后重载全部状态，不伪造原子成功', async () => {
    mockListOrgConfigs
      .mockResolvedValueOnce(response([
        status('ai.google.api_key', 4),
        status('ai.kie.api_key', 6),
      ]))
      .mockResolvedValueOnce(response([status('ai.kie.api_key', 7)]));
    mockDeleteOrgConfig
      .mockResolvedValueOnce({
        success: true,
        data: status('ai.google.api_key', 5, false),
      })
      .mockRejectedValueOnce(new Error('conflict'));
    render(<AiConfigSection orgId="org-a" />);
    await screen.findByText(/v4/);

    await switchToPlatform();

    await screen.findByText(/未能完全切换到平台服务/);
    expect(mockListOrgConfigs).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('radio', { name: /使用自有 AI Key/ })).toBeChecked();
    expect(screen.getByText(/v7/)).toBeInTheDocument();
  });
});

describe('AiConfigSection platform mode concurrency', () => {
  it('连续点击只执行一轮删除', async () => {
    const pending = deferred<{
      success: true;
      data: OrgConfigStatus;
    }>();
    mockListOrgConfigs
      .mockResolvedValueOnce(response([status('ai.google.api_key', 2)]))
      .mockResolvedValueOnce(response([]));
    mockDeleteOrgConfig.mockReturnValueOnce(pending.promise);
    render(<AiConfigSection orgId="org-a" />);
    const platform = await screen.findByRole(
      'radio',
      { name: /使用平台 AI 服务/ },
    );
    const user = userEvent.setup();

    await user.click(platform);
    await user.click(platform);
    expect(mockDeleteOrgConfig).toHaveBeenCalledTimes(1);

    pending.resolve({
      success: true,
      data: status('ai.google.api_key', 3, false),
    });
    await screen.findByText('已切换到平台 AI 服务');
  });

  it('权威状态重载失败时解除切换锁并允许重试', async () => {
    mockListOrgConfigs
      .mockResolvedValueOnce(response([status('ai.google.api_key', 2)]))
      .mockRejectedValueOnce(new Error('reload failed'))
      .mockResolvedValueOnce(response([status('ai.google.api_key', 3)]));
    render(<AiConfigSection orgId="org-a" />);
    await screen.findByText(/v2/);

    await switchToPlatform();

    await screen.findByText('无法确认服务端配置状态，请重试');
    expect(screen.getByRole(
      'radio',
      { name: /使用平台 AI 服务/ },
    )).not.toBeDisabled();
    await switchToPlatform();
    await waitFor(() => expect(mockDeleteOrgConfig).toHaveBeenCalledTimes(2));
  });

  it('orgId 变化后忽略旧企业切换结果', async () => {
    const pending = deferred<{
      success: true;
      data: OrgConfigStatus;
    }>();
    mockListOrgConfigs
      .mockResolvedValueOnce(response([status('ai.google.api_key', 2)]))
      .mockResolvedValueOnce(response([]));
    mockDeleteOrgConfig.mockReturnValueOnce(pending.promise);
    const view = render(<AiConfigSection orgId="org-a" />);
    await screen.findByText(/v2/);
    await switchToPlatform();

    view.rerender(<AiConfigSection orgId="org-b" />);
    await waitFor(() => {
      expect(mockListOrgConfigs).toHaveBeenCalledWith('org-b');
      expect(screen.getByRole(
        'radio',
        { name: /使用平台 AI 服务/ },
      )).toBeChecked();
    });
    pending.resolve({
      success: true,
      data: status('ai.google.api_key', 3, false),
    });

    await waitFor(() => {
      expect(mockListOrgConfigs).toHaveBeenCalledTimes(2);
      expect(screen.queryByText('已切换到平台 AI 服务')).not.toBeInTheDocument();
    });
  });

  it('卸载后忽略未完成切换的结果', async () => {
    const pending = deferred<{
      success: true;
      data: OrgConfigStatus;
    }>();
    mockListOrgConfigs.mockResolvedValueOnce(
      response([status('ai.google.api_key', 2)]),
    );
    mockDeleteOrgConfig.mockReturnValueOnce(pending.promise);
    const view = render(<AiConfigSection orgId="org-a" />);
    await screen.findByText(/v2/);
    await switchToPlatform();

    view.unmount();
    pending.resolve({
      success: true,
      data: status('ai.google.api_key', 3, false),
    });
    await Promise.resolve();

    expect(mockListOrgConfigs).toHaveBeenCalledTimes(1);
  });
});
