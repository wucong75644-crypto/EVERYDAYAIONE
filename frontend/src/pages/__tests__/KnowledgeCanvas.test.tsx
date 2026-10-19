import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import KnowledgeCanvas from '../KnowledgeCanvas';
const f = vi.hoisted(() => ({ api: { available: vi.fn(), initialize: vi.fn(), scene: vi.fn(), object: vi.fn(), command: vi.fn(), members: vi.fn() }, auth: { user: { id: 'u' }, currentOrgId: 'org' as string | null } }));
vi.mock('../../services/knowledge', () => ({ knowledgeApi: f.api }));
vi.mock('../../stores/useAuthStore', () => ({ useAuthStore: (selector: (s: typeof f.auth) => unknown) => selector(f.auth) }));
const node = { id: 'root', node_id: 'placement', container_id: 'p', parent_id: null, kind: 'topic', title: '项目总纲', revision: 1, layout_revision: 1, x: 20, y: 40 };
const initial = { containers: [{ id: 'p', kind: 'project', title: '客户项目', role: 'manager' }, { id: 'personal', kind: 'personal', title: '个人知识库', role: 'manager' }], nodes: [node], relations: [] };
function show() { return render(<MemoryRouter><KnowledgeCanvas /></MemoryRouter>); }
async function ready() { await screen.findByText('已连接'); }
beforeEach(() => {
  vi.resetAllMocks();
  HTMLElement.prototype.setPointerCapture = vi.fn();
  HTMLElement.prototype.hasPointerCapture = vi.fn(() => false);
  f.auth.currentOrgId = 'org';
  f.api.available.mockResolvedValue({ enabled: true }); f.api.initialize.mockResolvedValue(null);
  f.api.scene.mockResolvedValue(structuredClone(initial)); f.api.command.mockResolvedValue({ success: true, data: { object_id: 'new' } });
  f.api.object.mockResolvedValue({ ...node, body: '原文', can_write: true });
});
describe('知识画布 V1', () => {
  it('shows three tabs, no sidebar creation and no canvas chat', async () => {
    show(); await ready();
    expect(screen.getAllByRole('tab')).toHaveLength(3);
    const sidebar = screen.getByRole('complementary', { name: '知识目录' });
    expect(within(sidebar).queryByRole('button', { name: /创建/ })).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/对话|@/)).not.toBeInTheDocument();
    const world = document.querySelector('.kc-world')!; const before = world.getAttribute('style');
    fireEvent.click(screen.getByRole('tab', { name: '项目', exact: true }));
    expect(world.getAttribute('style')).toBe(before);
  });
  it('creates from canvas into the selected project and keeps no chat option', async () => {
    show(); await ready(); fireEvent.click(screen.getByRole('tab', { name: '项目', exact: true }));
    fireEvent.doubleClick(screen.getByLabelText('知识画布'), { clientX: 200, clientY: 200 });
    const menu = screen.getByRole('menu'); expect(within(menu).queryByText(/对话|定时/)).not.toBeInTheDocument();
    fireEvent.click(within(menu).getByRole('button', { name: '创建主题' }));
    const dialog = screen.getByRole('dialog', { name: '创建内容' });
    fireEvent.change(within(dialog).getByLabelText('标题'), { target: { value: '新分支' } });
    fireEvent.click(within(dialog).getByRole('button', { name: '保存到画布' }));
    await waitFor(() => expect(f.api.command).toHaveBeenCalledWith('create_object', expect.objectContaining({ container_id: 'p', title: '新分支', kind: 'topic' }), expect.any(String)));
  });
  it('preserves an editing draft when another writer caused a version conflict', async () => {
    show(); await ready(); fireEvent.click(screen.getByRole('button', { name: '展开编辑' }));
    const dialog = await screen.findByRole('dialog', { name: '编辑内容' });
    fireEvent.change(within(dialog).getByLabelText('正文'), { target: { value: '我尚未保存的修改' } });
    f.api.command.mockRejectedValue(new Error('内容已更新，请刷新'));
    fireEvent.click(within(dialog).getByRole('button', { name: '保存正文' }));
    await within(dialog).findByRole('alert');
    expect(within(dialog).getByLabelText('正文')).toHaveValue('我尚未保存的修改');
    expect(f.api.command).toHaveBeenCalledWith('save_document', { object_id: 'root', expected_revision: 1, body: '我尚未保存的修改' }, expect.any(String));
  });
  it('reuses a command identity after an uncertain response instead of creating twice', async () => {
    show(); await ready(); fireEvent.click(screen.getByRole('button', { name: '展开编辑' }));
    const dialog = await screen.findByRole('dialog', { name: '编辑内容' });
    f.api.command.mockRejectedValueOnce(new Error('network unavailable'));
    fireEvent.click(within(dialog).getByRole('button', { name: '保存正文' }));
    await within(dialog).findByRole('alert');
    const firstId = f.api.command.mock.calls[0][2];
    fireEvent.click(within(dialog).getByRole('button', { name: '保存正文' }));
    await waitFor(() => expect(f.api.command).toHaveBeenCalledTimes(2));
    expect(f.api.command.mock.calls[1][2]).toBe(firstId);
  });
  it('offers no save action for a viewer', async () => {
    f.api.scene.mockResolvedValue({ ...initial, containers: [{ ...initial.containers[0], role: 'viewer' }] });
    f.api.object.mockResolvedValue({ ...node, body: '只读内容', can_write: false });
    show(); await ready(); fireEvent.click(screen.getByRole('button', { name: '展开查看' }));
    const dialog = await screen.findByRole('dialog', { name: '编辑内容' });
    expect(within(dialog).getByLabelText('正文')).toHaveAttribute('readonly');
    expect(within(dialog).queryByRole('button', { name: '保存正文' })).not.toBeInTheDocument();
    expect(document.querySelector('.kc-handle')).not.toBeInTheDocument();
  });
  it('Enter on a root inherits its project even while personal navigation is active', async () => {
    show(); await ready(); const canvas = screen.getByLabelText('知识画布');
    fireEvent.contextMenu(screen.getByRole('article', { name: '项目总纲' }), { clientX: 200, clientY: 200 });
    fireEvent.click(screen.getByRole('button', { name: '关闭菜单' }));
    // A read/open action must not be necessary to create a sibling.
    fireEvent.pointerDown(screen.getByRole('article', { name: '项目总纲' }), { button: 0, pointerId: 1 });
    fireEvent.keyDown(canvas, { key: 'Enter' });
    const dialog = screen.getByRole('dialog', { name: '创建内容' });
    expect(within(dialog).getByLabelText('归属')).toHaveValue('p');
  });
  it('fails closed when the server does not enable this account', async () => {
    f.api.available.mockResolvedValue({ enabled: false });show();
    await screen.findByText('知识画布尚未向当前账号开放');
    expect(f.api.initialize).not.toHaveBeenCalled(); expect(f.api.scene).not.toHaveBeenCalled();
  });
  it('does not display the old organization while the next one is loading', async () => {
    const view=show();await ready();
    f.auth.currentOrgId='other';f.api.available.mockReturnValue(new Promise(() => {}));
    view.rerender(<MemoryRouter><KnowledgeCanvas /></MemoryRouter>);
    await screen.findByText('正在加载知识画布…');
    expect(screen.queryByRole('article', { name: '项目总纲' })).not.toBeInTheDocument();
  });
});
