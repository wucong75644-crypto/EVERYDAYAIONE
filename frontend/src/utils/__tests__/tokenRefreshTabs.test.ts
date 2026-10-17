import { afterEach, expect, it, vi } from 'vitest';
const { post, clearAuth } = vi.hoisted(() => ({ post: vi.fn(), clearAuth: vi.fn() }));
vi.mock('axios', () => ({ default: { post, isAxiosError: () => false } }));
vi.mock('../../services/api', () => ({ API_BASE_URL: '/api' }));
vi.mock('../../stores/useAuthStore', () => ({ useAuthStore: { getState: () => ({ clearAuth, setToken: vi.fn() }) } }));
vi.mock('../logger', () => ({ logger: { info: vi.fn(), warn: vi.fn() } }));
const originalLocks = navigator.locks;
afterEach(() => { Object.defineProperty(navigator, 'locks', { configurable: true, value: originalLocks }); localStorage.clear(); vi.resetModules(); });
it('two independent tab modules share one rotation and both receive the new token', async () => {
  vi.resetModules(); const tabA = await import('../tokenManager');
  vi.resetModules(); const tabB = await import('../tokenManager');
  let tail: Promise<unknown> = Promise.resolve();
  Object.defineProperty(navigator, 'locks', { configurable: true, value: {
    request: vi.fn((_name, _options, callback) => {
      const result = tail.then(callback); tail = result.catch(() => {}); return result;
    }),
  } });
  localStorage.setItem('user', JSON.stringify({ id: 'same-account' }));
  localStorage.setItem('refresh_token', 'old');
  let finish!: (value: unknown) => void;
  post.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const first = tabA.silentRefresh(); const second = tabB.silentRefresh();
  await vi.waitFor(() => expect(post).toHaveBeenCalledOnce());
  finish({ data: { token: { access_token: 'new-access', refresh_token: 'new-refresh' } } });
  await expect(Promise.all([first, second])).resolves.toEqual(['new-access', 'new-access']);
  expect(post).toHaveBeenCalledOnce(); expect(clearAuth).not.toHaveBeenCalled();
  expect(localStorage.getItem('refresh_token')).toBe('new-refresh');
});
