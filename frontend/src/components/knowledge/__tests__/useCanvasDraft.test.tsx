import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { createMemoryRouter, Link, RouterProvider } from 'react-router-dom';
import { useCanvasDraft } from '../useCanvasDraft';
function Page({ dirty = true }) { const { storageError } = useCanvasDraft('user:org', dirty ? { body: '草稿' } : null, true); return <><Link to="/away">离开</Link>{storageError && <p>本地写入失败</p>}</>; }
function setup(dirty = true) { const router = createMemoryRouter([{ path: '/', element: <Page dirty={dirty} /> }, { path: '/away', element: <p>其他页面</p> }]); render(<RouterProvider router={router} />); return router; }
beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
afterEach(() => vi.restoreAllMocks());
describe('draft navigation guard', () => {
  it('keeps the page when navigation is declined', async () => { const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false); setup(); fireEvent.click(screen.getByText('离开')); await waitFor(() => expect(confirm).toHaveBeenCalledOnce()); expect(screen.getByText('离开')).toBeInTheDocument(); });
  it('permits leaving and retains recovery content', async () => { vi.spyOn(window, 'confirm').mockReturnValue(true); setup(); fireEvent.click(screen.getByText('离开')); await screen.findByText('其他页面'); expect(Object.values(localStorage).join('')).toContain('草稿'); });
  it('allows pristine navigation without asking', async () => { const confirm = vi.spyOn(window, 'confirm'); setup(false); fireEvent.click(screen.getByText('离开')); await screen.findByText('其他页面'); expect(confirm).not.toHaveBeenCalled(); });
  it('protects router Back as well as links', async () => { const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false); const router = createMemoryRouter([{ path: '*', element: <Page /> }], { initialEntries: ['/previous', '/'], initialIndex: 1 }); render(<RouterProvider router={router} />); await act(() => router.navigate(-1)); await waitFor(() => expect(confirm).toHaveBeenCalledOnce()); expect(router.state.location.pathname).toBe('/'); });
});

it('warns about data loss rather than promising recovery when storage is unavailable', async () => {
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('quota exceeded', 'QuotaExceededError'); });
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false); setup();
  await screen.findByText('本地写入失败'); fireEvent.click(screen.getByText('离开'));
  await waitFor(() => expect(confirm).toHaveBeenCalledWith(expect.stringContaining('离开可能丢失内容')));
  const event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event); expect(event.defaultPrevented).toBe(true);
});

function DurableEditor({ name, identity, body }: { name: string; identity: string; body: string | null }) {
  const store = useCanvasDraft(identity, body ? { body } : null, true);
  return <section aria-label={name}><output>{store.recoveries.length}</output>{store.recovery && <p>{store.recovery.body}</p>}<button onClick={() => { if (store.claim()) store.accept(); }}>接管</button></section>;
}
function durable(name: string, identity: string, body: string | null) {
  const router = createMemoryRouter([{ path: '*', element: <DurableEditor name={name} identity={identity} body={body} /> }]);
  return render(<RouterProvider router={router} />);
}
it('recovers after closing a tab and losing all session storage', async () => {
  const first = durable('first', 'u:org', '关闭后保留'); first.unmount(); sessionStorage.clear();
  durable('reopened', 'u:org', null); expect(await screen.findByText('关闭后保留')).toBeInTheDocument();
});
it('keeps independent active tabs in separate slots and offers both after they close', async () => {
  const first = durable('first', 'u:org', '第一标签草稿'); const second = durable('second', 'u:org', '第二标签草稿');
  expect(Object.values(localStorage).join('')).toContain('第一标签草稿'); expect(Object.values(localStorage).join('')).toContain('第二标签草稿');
  const observer = durable('observer', 'u:org', null); expect(screen.getAllByRole('status').at(-1)).toHaveTextContent('0');
  first.unmount(); second.unmount(); act(() => window.dispatchEvent(new Event('storage')));
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('2'));
  observer.unmount();
});
it('migrates the old same-tab draft into durable storage', async () => {
  sessionStorage.setItem('everydayai:canvas-draft:v1:u:org', JSON.stringify({ identity: 'u:org', saved: Date.now(), value: { body: '旧版未保存内容' } }));
  const view = durable('migration', 'u:org', null); expect(await screen.findByText('旧版未保存内容')).toBeInTheDocument();
  expect(sessionStorage.getItem('everydayai:canvas-draft:v1:u:org')).toBeNull(); view.unmount(); sessionStorage.clear();
  durable('reopened', 'u:org', null); expect(await screen.findByText('旧版未保存内容')).toBeInTheDocument();
});
it('does not show another account or organization draft', async () => {
  const first = durable('first', 'u:org', '隔离草稿'); first.unmount();
  const otherUser = durable('user', 'someone:org', null); expect(screen.queryByText('隔离草稿')).not.toBeInTheDocument(); otherUser.unmount();
  durable('org', 'u:different', null); expect(screen.queryByText('隔离草稿')).not.toBeInTheDocument();
});

it('expires old inactive drafts without deleting another live editor', async () => {
  const first = durable('first', 'u:org', '过期内容'); first.unmount();
  const key = Object.keys(localStorage).find(k => k.startsWith('everydayai:canvas-drafts:v2:'))!;
  const record = JSON.parse(localStorage.getItem(key)!); record.saved = Date.now() - 8 * 86400000; record.lease = 0; localStorage.setItem(key, JSON.stringify(record));
  const liveId = 'live-draft'; const liveKey = key.slice(0, key.lastIndexOf(':') + 1) + liveId;
  localStorage.setItem(liveKey, JSON.stringify({ ...record, id: liveId, lease: Date.now() + 30000 }));
  durable('reopened', 'u:org', null); expect(screen.queryByText('过期内容')).not.toBeInTheDocument();
  expect(localStorage.getItem(key)).toBeNull(); expect(localStorage.getItem(liveKey)).not.toBeNull();
});
it('releases a closing tab so a new tab can discover its draft immediately', async () => {
  durable('first', 'u:org', '关闭事件保留');
  act(() => window.dispatchEvent(new Event('pagehide')));
  durable('reopened', 'u:org', null); expect(await screen.findByText('关闭事件保留')).toBeInTheDocument();
});
