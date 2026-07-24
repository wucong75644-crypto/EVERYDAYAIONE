import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import WecomCallback from '../WecomCallback';
import { exchangeWecomHandoff } from '../../services/auth';
import { useAuthStore } from '../../stores/useAuthStore';

vi.mock('../../services/auth', () => ({
  exchangeWecomHandoff: vi.fn(),
}));
vi.mock('../../stores/useAuthStore', () => ({
  useAuthStore: vi.fn(),
}));

const setTokens = vi.fn();
const setUser = vi.fn();
const setCurrentOrg = vi.fn();

function renderCallback(query: string) {
  return render(
    <MemoryRouter initialEntries={[`/auth/wecom/callback${query}`]}>
      <Routes>
        <Route path="/auth/wecom/callback" element={<WecomCallback />} />
        <Route path="/" element={<div>home</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('WecomCallback handoff', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    vi.mocked(useAuthStore).mockReturnValue({
      setTokens,
      setUser,
      setCurrentOrg,
    } as ReturnType<typeof useAuthStore>);
  });

  it('POST 消费一次性交接码并写入认证状态', async () => {
    vi.mocked(exchangeWecomHandoff).mockResolvedValue({
      token: {
        access_token: 'access',
        refresh_token: 'refresh',
        token_type: 'bearer',
        expires_in: 3600,
        refresh_expires_in: 86400,
      },
      user: {
        id: 'user-1',
        nickname: '张三',
        avatar_url: null,
        phone: null,
        role: 'user',
        credits: 100,
        created_at: '2026-07-24T00:00:00Z',
      },
      org: { org_id: 'org-1', name: '示例企业', role: 'member' },
    });

    renderCallback('?handoff=opaque-code');

    await waitFor(() => {
      expect(exchangeWecomHandoff).toHaveBeenCalledWith(
        'opaque-code', expect.any(AbortSignal),
      );
    });
    expect(setTokens).toHaveBeenCalledWith('access', 'refresh');
    expect(setUser).toHaveBeenCalledWith(expect.objectContaining({ id: 'user-1' }));
    expect(setCurrentOrg).toHaveBeenCalledWith(
      expect.objectContaining({ org_id: 'org-1' }),
    );
    expect(await screen.findByText('home')).toBeInTheDocument();
  });

  it('交接码失效时显示重新扫码提示', async () => {
    vi.mocked(exchangeWecomHandoff).mockRejectedValue(new Error('expired'));
    renderCallback('?handoff=expired-code');
    expect(
      await screen.findByText('登录交接码已失效，请重新扫码'),
    ).toBeInTheDocument();
  });

  it('不再接受 URL 中的 token 和 user 数据', async () => {
    renderCallback('?token=legacy-token&user=legacy-user');
    expect(await screen.findByText('无效的回调参数')).toBeInTheDocument();
    expect(exchangeWecomHandoff).not.toHaveBeenCalled();
  });

  it('组件卸载时中止尚未完成的交换请求', async () => {
    let signal: AbortSignal | undefined;
    vi.mocked(exchangeWecomHandoff).mockImplementation((_code, currentSignal) => {
      signal = currentSignal;
      return new Promise(() => undefined);
    });
    const view = renderCallback('?handoff=pending-code');
    await waitFor(() => expect(signal).toBeDefined());
    view.unmount();
    expect(signal?.aborted).toBe(true);
  });
});
