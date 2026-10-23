import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import KnowledgeCanvas from '../KnowledgeCanvas';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { createContext, useContext, useState } from 'react';
import userEvent from '@testing-library/user-event';
const TestContent = createContext<React.ReactNode>(null);
function Content() { return useContext(TestContent); }
function MemoryRouter({ children }: { children: React.ReactNode }) { const [router] = useState(() => createMemoryRouter([{ path: '*', element: <Content /> }])); return <TestContent.Provider value={children}><RouterProvider router={router} /></TestContent.Provider>; }

const f = vi.hoisted(() => ({ api: { captureSnapshot: vi.fn(), history: vi.fn(), available: vi.fn(), initialize: vi.fn(), scene: vi.fn(), object: vi.fn(), command: vi.fn(), members: vi.fn(), versions: vi.fn(), version: vi.fn(), search: vi.fn() }, files: { list: vi.fn() }, auth: { user: { id: 'u' }, currentOrgId: 'org' as string | null } }));
vi.mock('../../services/knowledge', () => ({ knowledgeApi: f.api, knowledgeFilesApi: f.files }));
vi.mock('../../stores/useAuthStore', () => ({ useAuthStore: (selector: (s: typeof f.auth) => unknown) => selector(f.auth) }));
const node = { id: 'root', node_id: 'placement', container_id: 'p', parent_id: null, kind: 'topic', title: '项目总纲', revision: 1, layout_revision: 1, x: 20, y: 40 };
const initial = { containers: [{ id: 'p', kind: 'project', title: '客户项目', role: 'manager', revision: 12 }, { id: 'personal', kind: 'personal', title: '个人知识库', role: 'manager' }], nodes: [node], relations: [] };
function show() { return render(<MemoryRouter><KnowledgeCanvas /></MemoryRouter>); }
async function snapshotSettled() { await waitFor(() => expect(screen.queryByText('正在固定复制内容快照…')).not.toBeInTheDocument()); }
async function ready() { await screen.findByText('已连接'); }
beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal('matchMedia', vi.fn((query: string) => ({ matches: true, media: query, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
  sessionStorage.clear(); localStorage.clear();
  HTMLElement.prototype.setPointerCapture = vi.fn();
  HTMLElement.prototype.hasPointerCapture = vi.fn(() => false);
  f.auth.user.id = 'u'; f.auth.currentOrgId = 'org';
  f.files.list.mockResolvedValue([]);
  f.api.captureSnapshot.mockResolvedValue({ success: true, data: {} });
  f.api.history.mockResolvedValue({ undo: null, redo: null });
  f.api.available.mockResolvedValue({ enabled: true }); f.api.initialize.mockResolvedValue(null);
  f.api.scene.mockResolvedValue(structuredClone(initial)); f.api.command.mockResolvedValue({ success: true, data: { object_id: 'new' } });
  f.api.object.mockResolvedValue({ ...node, body: '原文', can_write: true });
});
afterEach(() => vi.unstubAllGlobals());
describe('知识画布', () => {
  it('keeps an unsaved body while reading a previous version', async () => {
    f.api.versions.mockResolvedValue({ items: [{ revision: 1, title: '原始标题', created_at: '2026-10-22T00:00:00Z' }], next_cursor: null });
    f.api.version.mockResolvedValue({ revision: 1, title: '原始标题', body: '历史正文' });
    show(); await ready(); fireEvent.click(screen.getByRole('button', { name: '展开编辑' }));
    const dialog = await screen.findByRole('dialog', { name: '编辑内容' });
    fireEvent.change(within(dialog).getByLabelText('正文'), { target: { value: '未保存的新正文' } });
    fireEvent.click(within(dialog).getByText('查看历史版本'));
    fireEvent.click(await within(dialog).findByRole('button', { name: /版本 1/ }));
    expect(await within(dialog).findByLabelText('历史版本正文')).toHaveValue('历史正文');
    expect(within(dialog).getByLabelText('正文')).toHaveValue('未保存的新正文');
    expect(f.api.command).not.toHaveBeenCalled();
  });
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
    const input = screen.getByRole('textbox', { name: '新主题标题' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    fireEvent.change(input, { target: { value: '新分支' } });
    fireEvent.keyDown(input, { key: 'Enter' });
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
    fireEvent.click(screen.getByRole('menuitem', { name: '关闭菜单' }));
    // A read/open action must not be necessary to create a sibling.
    fireEvent.pointerDown(screen.getByRole('article', { name: '项目总纲' }), { button: 0, pointerId: 1 });
    fireEvent.keyDown(canvas, { key: 'Enter' });
    fireEvent.change(screen.getByLabelText('新主题标题'), { target: { value: '同级主题' } });
    fireEvent.keyDown(screen.getByLabelText('新主题标题'), { key: 'Enter' });
    await waitFor(() => expect(f.api.command).toHaveBeenCalledWith('create_object', expect.objectContaining({ container_id: 'p', parent_id: null }), expect.any(String)));
  });
  function selectRoot() { fireEvent.pointerDown(screen.getByRole('article', { name: '项目总纲' }), { button: 0, pointerId: 1 }); }
  function persistCreatedNodes() {
    const state = structuredClone(initial);
    f.api.command.mockImplementation(async (operation, payload) => {
      if (operation === 'create_object') {
        const id = 'created-' + state.nodes.length;
        state.nodes.push({ ...node, id, node_id: id, title: payload.title, container_id: payload.container_id, parent_id: payload.parent_id, ...payload.position });
        f.api.scene.mockResolvedValue(structuredClone(state));
        return { success: true, data: { object_id: id } };
      }
      return { success: true, data: { object_id: null } };
    });
  }
  it('creates a child and continues siblings then children without opening a dialog', async () => {
    persistCreatedNodes(); show(); await ready(); selectRoot();
    fireEvent.keyDown(screen.getByLabelText('知识画布'), { key: 'Tab' });
    let input = screen.getByLabelText('新主题标题');
    expect(input).toHaveFocus();
    fireEvent.change(input, { target: { value: '第一条' } }); fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(screen.getByRole('article', { name: '第一条' })).toBeInTheDocument());
    input = screen.getByLabelText('新主题标题');
    expect(input).toHaveValue(''); expect(input).toHaveFocus();
    fireEvent.change(input, { target: { value: '第二条' } }); fireEvent.keyDown(input, { key: 'Tab' });
    await screen.findByRole('article', { name: '第二条' });
    input = screen.getByLabelText('新主题标题'); fireEvent.change(input, { target: { value: '下一级' } }); fireEvent.keyDown(input, { key: 'Enter' });
    await screen.findByRole('article', { name: '下一级' });
    expect(f.api.command.mock.calls.map(c => c[1].parent_id)).toEqual(['root', 'root', 'created-2']);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
  it('places repeated children outside occupied cards and reveals a folded branch', async () => {
    persistCreatedNodes(); show(); await ready(); selectRoot();
    fireEvent.keyDown(screen.getByLabelText('知识画布'), { key: 'Tab' });
    fireEvent.change(screen.getByLabelText('新主题标题'), { target: { value: '第一个' } });
    fireEvent.blur(screen.getByLabelText('新主题标题'));
    await screen.findByRole('article', { name: '第一个' });
    fireEvent.click(screen.getByRole('button', { name: '折叠或展开 项目总纲' })); selectRoot();
    fireEvent.keyDown(screen.getByLabelText('知识画布'), { key: 'Tab' });
    expect(screen.getByRole('article', { name: '第一个' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('新主题标题'), { target: { value: '第二个' } }); fireEvent.blur(screen.getByLabelText('新主题标题'));
    await screen.findByRole('article', { name: '第二个' });
    const first = f.api.command.mock.calls[0][1].position, second = f.api.command.mock.calls[1][1].position;
    expect(second.x).toBe(first.x); expect(second.y - first.y).toBeGreaterThanOrEqual(194);
  });
  it('renames inline with double click, preserves body access and returns keyboard focus', async () => {
    show(); await ready(); fireEvent.doubleClick(screen.getByRole('article', { name: '项目总纲' }));
    const input = screen.getByLabelText('节点标题'); expect(input).toHaveValue('项目总纲');
    fireEvent.change(input, { target: { value: '新总纲' } }); fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(f.api.command).toHaveBeenCalledWith('update_title', { object_id: 'root', expected_revision: 1, title: '新总纲' }, expect.any(String)));
    await waitFor(() => expect(screen.getByLabelText('知识画布')).toHaveFocus());
    expect(f.api.object).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole('button', { name: '展开编辑' }));
    expect(await screen.findByRole('dialog', { name: '编辑内容' })).toBeInTheDocument();
  });
  it('ignores IME confirmation and modified Enter on both platforms', async () => {
    show(); await ready(); selectRoot(); fireEvent.keyDown(screen.getByLabelText('知识画布'), { key: 'Tab' });
    const input = screen.getByLabelText('新主题标题'); fireEvent.change(input, { target: { value: '中文主题' } });
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true });
    fireEvent.keyDown(input, { key: 'Enter', keyCode: 229 });
    fireEvent.keyDown(input, { key: 'Enter', metaKey: true }); fireEvent.keyDown(input, { key: 'Enter', ctrlKey: true });
    expect(f.api.command).not.toHaveBeenCalled(); fireEvent.keyDown(input, { key: 'Escape' });
    expect(screen.queryByLabelText('新主题标题')).not.toBeInTheDocument(); expect(f.api.command).not.toHaveBeenCalled();
  });
  it('keeps failed inline input and reuses the command identity on retry', async () => {
    show(); await ready(); selectRoot(); fireEvent.keyDown(screen.getByLabelText('知识画布'), { key: 'Tab' });
    const input = screen.getByLabelText('新主题标题'); fireEvent.change(input, { target: { value: '保留内容' } });
    f.api.command.mockRejectedValueOnce(new Error('network unavailable')); fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(input).not.toHaveAttribute('readonly')); expect(input).toHaveValue('保留内容');
    await waitFor(() => expect(screen.getAllByRole('alert').length).toBeGreaterThan(0));
    const id = f.api.command.mock.calls[0][2]; fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(f.api.command).toHaveBeenCalledTimes(2)); expect(f.api.command.mock.calls[1][2]).toBe(id);
  });
  it('does not offer inline edits or creation for a viewer', async () => {
    f.api.scene.mockResolvedValue({ ...initial, containers: [{ ...initial.containers[0], role: 'viewer' }] });
    show(); await ready(); fireEvent.doubleClick(screen.getByRole('article', { name: '项目总纲' }));
    expect(await screen.findByRole('dialog', { name: '编辑内容' })).toBeInTheDocument();
    expect(screen.queryByLabelText('节点标题')).not.toBeInTheDocument(); expect(screen.queryByRole('button', { name: /创建子级/ })).not.toBeInTheDocument();
    fireEvent.keyDown(screen.getByLabelText('知识画布'), { key: 'Tab' }); expect(screen.queryByLabelText('新主题标题')).not.toBeInTheDocument();
  });
  it('leaves simple clicks uncaptured and ignores pointer jitter before dragging', async () => {
    show(); await ready(); const root = screen.getByRole('article', { name: '项目总纲' });
    fireEvent.pointerDown(root, { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
    expect(HTMLElement.prototype.setPointerCapture).not.toHaveBeenCalled();
    fireEvent.pointerUp(screen.getByLabelText('知识画布'), { pointerId: 1 });
    expect(f.api.command).not.toHaveBeenCalled();
  });
  it('confirms an uncertain inline save through refresh without leaving a duplicate draft', async () => {
    show(); await ready(); selectRoot(); fireEvent.keyDown(screen.getByLabelText('知识画布'), { key: 'Tab' });
    const input = screen.getByLabelText('新主题标题'); fireEvent.change(input, { target: { value: '不重复创建' } });
    f.api.command.mockRejectedValueOnce(new Error('network unavailable')); fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(screen.getAllByRole('alert').length).toBeGreaterThan(0));
    const id = f.api.command.mock.calls[0][2]; fireEvent.click(screen.getByRole('button', { name: '刷新' }));
    await waitFor(() => expect(screen.queryByLabelText('新主题标题')).not.toBeInTheDocument());
    expect(f.api.command.mock.calls[1][2]).toBe(id);
  });
  function prepareLineGesture(extraNode = false) {
    class TestPointerEvent extends MouseEvent { pointerId: number; constructor(type: string, options: PointerEventInit = {}) { super(type, options); this.pointerId = options.pointerId ?? 1; } }
    vi.stubGlobal('PointerEvent', TestPointerEvent);
    if (extraNode) f.api.scene.mockResolvedValue({ ...initial, nodes: [node, { ...node, id: 'target', title: '另一个主题', x: 500 }] });
  }
  function lineGeometry() {
    const box = (x: number, y: number, width: number, height: number) => ({ x, y, left: x, top: y, width, height, right: x + width, bottom: y + height, toJSON: () => ({}) }) as DOMRect;
    const canvas = screen.getByLabelText('知识画布'); canvas.getBoundingClientRect = () => box(0, 0, 900, 700);
    screen.getByRole('article', { name: '项目总纲' }).getBoundingClientRect = () => box(80, 135, 240, 120);
    screen.queryByRole('article', { name: '另一个主题' })?.setAttribute('data-test-target', 'true');
    const target = document.querySelector<HTMLElement>('[data-test-target]'); if (target) target.getBoundingClientRect = () => box(560, 135, 240, 120);
    Object.defineProperty(document, 'elementFromPoint', { configurable: true, value: () => canvas });
    fireEvent.pointerDown(screen.getByRole('button', { name: '从 项目总纲 拉线创建' }), { button: 0, pointerId: 1, clientX: 320, clientY: 190 });
    return canvas;
  }
  it('creates from a dragged line inline, then continues child extensions and returns to the new handle', async () => {
    prepareLineGesture(); persistCreatedNodes(); show(); await ready(); const canvas = lineGeometry();
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 450, clientY: 400 });
    expect(screen.getByLabelText('拉线预览')).toBeInTheDocument(); expect(screen.getByText('松手创建子主题 · Esc 取消')).toBeInTheDocument();
    fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 450, clientY: 400 });
    expect(screen.queryByLabelText('拉线预览')).not.toBeInTheDocument(); expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(document.querySelector('.kc-draft-edge')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('新主题标题'), { target: { value: '拉线子级' } }); fireEvent.keyDown(screen.getByLabelText('新主题标题'), { key: 'Tab' });
    await screen.findByRole('article', { name: '拉线子级' });
    fireEvent.change(screen.getByLabelText('新主题标题'), { target: { value: '继续延伸' } }); fireEvent.keyDown(screen.getByLabelText('新主题标题'), { key: 'Enter' });
    await screen.findByRole('article', { name: '继续延伸' });
    expect(screen.queryByLabelText('新主题标题')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '从 继续延伸 拉线创建' })).toBeInTheDocument();
    expect(f.api.command.mock.calls.map(c => c[1].parent_id)).toEqual(['root', 'created-1']);
  });
  it('keeps the exact line drop despite nearby occupied layout and permits replacing an empty draft', async () => {
    prepareLineGesture(); persistCreatedNodes();
    f.api.scene.mockResolvedValue({ ...initial, nodes: [node, { ...node, id: 'occupied', node_id: 'occupied-layout', title: '已有节点', x: 400, y: 300 }] });
    show(); await ready(); const canvas = lineGeometry();
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 450, clientY: 400 }); fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 450, clientY: 400 });
    expect(screen.getByLabelText('新主题标题')).toBeInTheDocument();
    fireEvent.pointerDown(screen.getByLabelText('从 项目总纲 拉线创建'), { button: 0, pointerId: 2, clientX: 320, clientY: 190 });
    fireEvent.pointerMove(canvas, { pointerId: 2, clientX: 460, clientY: 390 }); fireEvent.pointerUp(canvas, { pointerId: 2, clientX: 460, clientY: 390 });
    expect(f.api.command).not.toHaveBeenCalled();
    const draft = document.querySelector<HTMLElement>('.kc-quick')!;
    const wanted = { x: parseFloat(draft.style.left), y: parseFloat(draft.style.top) };
    fireEvent.change(screen.getByLabelText('新主题标题'), { target: { value: '落点主题' } }); fireEvent.keyDown(screen.getByLabelText('新主题标题'), { key: 'Enter' });
    await waitFor(() => expect(f.api.command).toHaveBeenCalledWith('create_object', expect.objectContaining({ position: wanted, title: '落点主题' }), expect.any(String)));
    expect(screen.queryByText('主题 · 项目总纲')).toBeNull();
  });
  it('continues an empty new topic with Tab using its default title', async () => {
    prepareLineGesture(); persistCreatedNodes(); show(); await ready(); const canvas = lineGeometry();
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 450, clientY: 400 }); fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 450, clientY: 400 });
    fireEvent.keyDown(screen.getByLabelText('新主题标题'), { key: 'Tab' });
    await screen.findByRole('article', { name: '新主题' });
    expect(screen.getByLabelText('新主题标题')).toBeInTheDocument();
    expect(f.api.command.mock.calls[0][1].title).toBe('新主题');
  });
  it('snaps to an existing node and sends related rather than reparent', async () => {
    prepareLineGesture(true); show(); await ready(); const canvas = lineGeometry();
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 545, clientY: 180 });
    expect(screen.getByRole('article', { name: '另一个主题' })).toHaveClass('kc-drop-target');
    expect(screen.getByText('松手建立关联 · Esc 取消')).toBeInTheDocument();
    fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 545, clientY: 180 });
    await waitFor(() => expect(f.api.command).toHaveBeenCalledWith('connect', { source_object_id: 'root', target_object_id: 'target', relation_type: 'related' }, expect.any(String)));
    expect(screen.queryByLabelText('新主题标题')).not.toBeInTheDocument();
  });
  it('cancels a line with Escape or pointer cancellation without creating on release', async () => {
    prepareLineGesture(); show(); await ready(); const canvas = lineGeometry();
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 450, clientY: 400 }); fireEvent.keyDown(canvas, { key: 'Escape' });
    fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 450, clientY: 400 });
    expect(screen.queryByLabelText('新主题标题')).not.toBeInTheDocument(); expect(f.api.command).not.toHaveBeenCalled();
    fireEvent.pointerDown(screen.getByRole('button', { name: '从 项目总纲 拉线创建' }), { button: 0, pointerId: 1, clientX: 320, clientY: 190 });
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 450, clientY: 400 }); fireEvent.pointerCancel(canvas, { pointerId: 1 });
    fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 450, clientY: 400 });
    await waitFor(() => expect(screen.queryByLabelText('拉线预览')).not.toBeInTheDocument()); expect(f.api.command).not.toHaveBeenCalled();
  });
  it('moves an entire folded branch with one atomic command', async () => {
    prepareLineGesture();
    const child = { ...node, id: 'child', node_id: 'child-layout', parent_id: 'root', x: 350, y: 40, title: '隐藏子级' };
    f.api.scene.mockResolvedValue({ ...initial, nodes: [node, child] });
    show(); await ready(); fireEvent.click(screen.getByRole('button', { name: '折叠或展开 项目总纲' }));
    const canvas = screen.getByLabelText('知识画布'); canvas.getBoundingClientRect = () => ({ left: 0, top: 0, right: 900, bottom: 700, width: 900, height: 700 }) as DOMRect;
    fireEvent.pointerDown(screen.getByRole('article', { name: '项目总纲' }), { button: 0, pointerId: 1, clientX: 100, clientY: 150 });
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 180, clientY: 240 });
    expect(screen.getByText('松手移动整条分支 · Esc 取消')).toBeInTheDocument();
    fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 180, clientY: 240 });
    await waitFor(() => expect(f.api.command).toHaveBeenCalledWith('move_branch', { object_id: 'root', expected_revision: 1, expected_container_revision: 12, target_id: null, placement: 'free', nodes: [
      { node_id: 'placement', expected_revision: 1, position: { x: 100, y: 130 } }, { node_id: 'child-layout', expected_revision: 1, position: { x: 430, y: 130 } },
    ] }, expect.any(String)));
  });
  it('restores branch positions when Escape cancels a drag', async () => {
    prepareLineGesture(); show(); await ready(); const canvas = screen.getByLabelText('知识画布'); const article = screen.getByRole('article', { name: '项目总纲' });
    const before = article.getAttribute('style');
    fireEvent.pointerDown(article, { button: 0, pointerId: 1, clientX: 100, clientY: 150 });
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 180, clientY: 240 }); fireEvent.keyDown(canvas, { key: 'Escape' });
    expect(article.getAttribute('style')).toBe(before); expect(document.querySelector('.kc-insert-marker')).not.toBeInTheDocument();
    fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 180, clientY: 240 }); expect(f.api.command).not.toHaveBeenCalled();
  });
  it('shows the insertion marker and rolls back a rejected branch drop', async () => {
    prepareLineGesture(true); show(); await ready();
    const canvas = screen.getByLabelText('知识画布'); canvas.getBoundingClientRect = () => ({ left: 0, top: 0, right: 900, bottom: 700, width: 900, height: 700 }) as DOMRect;
    const article = screen.getByRole('article', { name: '项目总纲' }), before = article.getAttribute('style');
    f.api.command.mockRejectedValueOnce(Object.assign(new Error('布局已更新'), { status: 409 }));
    fireEvent.pointerDown(article, { button: 0, pointerId: 1, clientX: 100, clientY: 150 });
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 600, clientY: 140 });
    expect(document.querySelector('.kc-insert-marker')).toBeInTheDocument();
    expect(screen.getByText('插入「另一个主题」之前 · Esc 取消')).toBeInTheDocument();
    fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 600, clientY: 140 });
    await screen.findByRole('alert'); expect(article.getAttribute('style')).toBe(before);
    expect(f.api.command).toHaveBeenCalledWith('move_branch', expect.objectContaining({ placement: 'before', target_id: 'target' }), expect.any(String));
  });
  const undoEntry = { history_id: 'undo-step', operation: 'create_object', can_apply: true, reason: '' };
  const redoEntry = { ...undoEntry, history_id: 'redo-step' };
  it('undoes creation and redoes it with toolbar controls and fresh server history', async () => {
    f.api.history.mockResolvedValue({ undo: undoEntry, redo: null });
    f.api.command.mockImplementation(async (operation) => {
      if (operation === 'undo_canvas') { f.api.scene.mockResolvedValue({ ...initial, nodes: [] }); f.api.history.mockResolvedValue({ undo: null, redo: redoEntry }); }
      if (operation === 'redo_canvas') { f.api.scene.mockResolvedValue(initial); f.api.history.mockResolvedValue({ undo: undoEntry, redo: null }); }
      return { success: true, data: { object_id: null } };
    });
    show(); await ready(); fireEvent.click(screen.getByRole('button', { name: '撤销 创建节点' }));
    await waitFor(() => expect(screen.queryByRole('article', { name: '项目总纲' })).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '重做 创建节点' }));
    await screen.findByRole('article', { name: '项目总纲' });
    expect(f.api.command.mock.calls.map(c => c.slice(0, 2))).toEqual([['undo_canvas', { history_id: 'undo-step' }], ['redo_canvas', { history_id: 'redo-step' }]]);
  });
  it.each([{ key: 'z', metaKey: true }, { key: 'z', ctrlKey: true }])('supports Mac/Windows undo shortcut %j', async keys => {
    f.api.history.mockResolvedValue({ undo: undoEntry, redo: null }); show(); await ready();
    fireEvent.keyDown(screen.getByLabelText('知识画布'), keys);
    await waitFor(() => expect(f.api.command).toHaveBeenCalledWith('undo_canvas', { history_id: 'undo-step' }, expect.any(String)));
  });
  it.each([{ key: 'z', metaKey: true, shiftKey: true }, { key: 'y', ctrlKey: true }, { key: 'z', ctrlKey: true, shiftKey: true }])('supports Mac/Windows redo shortcut %j', async keys => {
    f.api.history.mockResolvedValue({ undo: null, redo: redoEntry }); show(); await ready();
    fireEvent.keyDown(screen.getByLabelText('知识画布'), keys);
    await waitFor(() => expect(f.api.command).toHaveBeenCalledWith('redo_canvas', { history_id: 'redo-step' }, expect.any(String)));
  });
  it('keeps title and body native undo separate from persistent canvas undo', async () => {
    f.api.history.mockResolvedValue({ undo: undoEntry, redo: null }); show(); await ready();
    fireEvent.doubleClick(screen.getByRole('article', { name: '项目总纲' }));
    const title = screen.getByLabelText('节点标题'), nativeTitleUndo = new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true });
    fireEvent(title, nativeTitleUndo); expect(nativeTitleUndo.defaultPrevented).toBe(false); expect(f.api.command).not.toHaveBeenCalled();
    fireEvent.keyDown(title, { key: 'Escape' }); fireEvent.click(screen.getByRole('button', { name: '展开编辑' }));
    const body = await screen.findByLabelText('正文'); const nativeBodyUndo = new KeyboardEvent('keydown', { key: 'z', metaKey: true, bubbles: true, cancelable: true });
    fireEvent(body, nativeBodyUndo); expect(nativeBodyUndo.defaultPrevented).toBe(false); expect(f.api.command).not.toHaveBeenCalled();
  });
  it('disables stale history after a conflict without optimistically popping it', async () => {
    f.api.history.mockResolvedValueOnce({ undo: undoEntry, redo: null }).mockResolvedValue({ undo: { ...undoEntry, can_apply: false, reason: '知识已更新' }, redo: null });
    f.api.command.mockRejectedValue(Object.assign(new Error('知识已更新，未覆盖新修改'), { status: 409 }));
    show(); await ready(); fireEvent.click(screen.getByRole('button', { name: '撤销 创建节点' }));
    await screen.findByRole('alert'); expect(screen.getByRole('article', { name: '项目总纲' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '撤销 创建节点' })).toBeDisabled();
    fireEvent.keyDown(screen.getByLabelText('知识画布'), { key: 'z', ctrlKey: true }); expect(f.api.command).toHaveBeenCalledTimes(1);
  });
  it('does not mistake a history-read failure for a failed committed command', async () => {
    f.api.history.mockResolvedValueOnce({ undo: undoEntry, redo: null }).mockRejectedValue(new Error('history read failed'));
    show(); await ready(); fireEvent.click(screen.getByRole('button', { name: '撤销 创建节点' }));
    await screen.findByText('撤销记录暂不可用，请刷新'); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '撤销' })).toBeDisabled();
  });
  it('can still open a fixed historical source when its created node is no longer on the canvas', async () => {
    window.history.replaceState({}, '', '/knowledge?object=root&revision=1');
    f.api.scene.mockResolvedValue({ ...initial, nodes: [] });
    f.api.version.mockResolvedValue({ object_id: 'root', revision: 1, title: '历史节点', body: '固定版本仍保留', created_at: '2026-10-23' });
    try { show(); await ready(); expect(await screen.findByLabelText('回答来源正文')).toHaveValue('固定版本仍保留'); }
    finally { window.history.replaceState({}, '', '/'); }
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

describe('整支批量操作', () => {
  const child = { ...node, id: 'child', node_id: 'child-layout', parent_id: 'root', title: '子主题', x: 350, y: 40 };
  function select(name: string, modifiers = {}) { fireEvent.pointerDown(screen.getByRole('article', { name }), { button: 0, pointerId: 1, ...modifiers }); fireEvent.pointerUp(screen.getByLabelText('知识画布'), { pointerId: 1 }); }
  beforeEach(() => { class TestPointerEvent extends MouseEvent { pointerId: number; constructor(type: string, options: PointerEventInit = {}) { super(type, options); this.pointerId = options.pointerId ?? 1; } } vi.stubGlobal('PointerEvent', TestPointerEvent); f.api.scene.mockResolvedValue({ ...structuredClone(initial), nodes: [node, child] }); });
  it.each(['metaKey', 'ctrlKey', 'shiftKey'])('supports %s click selection and deduplicates descendant payload', async modifier => {
    show(); await ready(); select('项目总纲'); select('子主题', { [modifier]: true });
    expect(screen.getByText('已选 2 个节点（含后代 2 个）')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '删除分支', exact: true }));
    await waitFor(() => expect(f.api.command).toHaveBeenCalledWith('delete_branches', expect.objectContaining({ container_id: 'p', objects: [{ object_id: 'root', expected_revision: 1 }, { object_id: 'child', expected_revision: 1 }] }), expect.any(String)));
  });
  it('copies complete branch and pastes under selected node using current revision', async () => {
    show(); await ready(); select('项目总纲');
    const transfer = { setData: vi.fn(), getData: vi.fn() };
    fireEvent.copy(screen.getByLabelText('知识画布'), { clipboardData: transfer });
    const record = JSON.parse(transfer.setData.mock.calls.find(c => c[0] === 'application/x-everydayai-branches')![1]);
    expect(record.objects).toHaveLength(2);
    fireEvent.paste(screen.getByLabelText('知识画布'), { clipboardData: { getData: () => JSON.stringify(record) } });
    await waitFor(() => expect(f.api.command).toHaveBeenCalledWith('copy_branches', expect.objectContaining({ parent_id: 'root', expected_container_revision: 12, objects: record.objects }), expect.any(String)));
  });
  it.each(['Delete','Backspace'])('deletes full branch using %s and exposes undo', async key => {
    show(); await ready(); select('项目总纲'); f.api.history.mockResolvedValue({ undo: { history_id: 'delete-step', operation: 'delete_branches', can_apply: true }, redo: null });
    fireEvent.keyDown(screen.getByLabelText('知识画布'), { key });
    await waitFor(() => expect(f.api.command).toHaveBeenCalledWith('delete_branches', expect.objectContaining({ objects: [{ object_id: 'root', expected_revision: 1 }] }), expect.any(String)));
    const undo = await screen.findByRole('button', { name: '撤销 删除分支' });
    await waitFor(() => expect(undo).toBeEnabled()); fireEvent.click(undo);
    await waitFor(() => expect(f.api.command).toHaveBeenCalledWith('undo_canvas', { history_id: 'delete-step' }, expect.any(String)));
  });
  it('writes button copy on body and accepts it through native paste without duplicating a command', async () => {
    show(); await ready(); select('项目总纲');
    const values = new Map<string, string>();
    const transfer = { setData: (type: string, value: string) => values.set(type, value), getData: (type: string) => values.get(type) ?? '' };
    const exec = vi.fn(() => { fireEvent.copy(document.body, { clipboardData: transfer }); return true; });
    Object.defineProperty(document, 'execCommand', { configurable: true, value: exec });
    try {
      fireEvent.click(screen.getByRole('button', { name: '复制分支', exact: true }));await snapshotSettled();
      expect(exec).toHaveBeenCalledWith('copy'); expect(values.has('application/x-everydayai-branches')).toBe(true);
      fireEvent.keyDown(screen.getByLabelText('知识画布'), { key: 'c', ctrlKey: true });
      expect(exec).toHaveBeenCalledTimes(2);
      fireEvent.paste(document.body, { clipboardData: transfer });
      await waitFor(() => expect(f.api.command).toHaveBeenCalledTimes(1));
      expect(f.api.command.mock.calls[0][0]).toBe('copy_branches');
    } finally { Reflect.deleteProperty(document, 'execCommand'); }
  });
  it('keeps native editing clipboard and deletion outside canvas history', async () => {
    show(); await ready(); select('项目总纲'); fireEvent.keyDown(screen.getByLabelText('知识画布'), { key: 'F2' });
    const input = screen.getByLabelText('节点标题'); const transfer = { setData: vi.fn() };
    fireEvent.copy(input, { clipboardData: transfer }); fireEvent.keyDown(input, { key: 'Backspace' });
    expect(transfer.setData).not.toHaveBeenCalled(); expect(f.api.command).not.toHaveBeenCalled();
  });
  it('selects visible active space with Cmd/Ctrl+A and clears with Escape', async () => {
    show(); await ready(); fireEvent.click(screen.getByRole('tab', { name: '项目', exact: true }));
    const canvas = screen.getByLabelText('知识画布'); fireEvent.keyDown(canvas, { key: 'a', ctrlKey: true });
    expect(screen.getByText('已选 2 个节点（含后代 2 个）')).toBeInTheDocument();
    fireEvent.keyDown(canvas, { key: 'Escape' }); expect(screen.getByRole('button', { name: '删除分支', exact: true })).toBeDisabled();
  });
  it('marquee selects cards and Escape cancels selection gesture without mutation', async () => {
    show(); await ready(); fireEvent.click(screen.getByRole('tab', { name: '项目', exact: true }));
    const canvas = screen.getByLabelText('知识画布'); fireEvent.pointerDown(canvas, { button: 0, pointerId: 1, clientX: 65, clientY: 100 });
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 700, clientY: 300 });
    expect(screen.getByText('已选 2 个节点（含后代 2 个）')).toBeInTheDocument();
    fireEvent.keyDown(canvas, { key: 'Escape' }); expect(document.querySelector('.kc-marquee')).toBeNull(); expect(f.api.command).not.toHaveBeenCalled();
  });
});

describe('多选拖动与剪切', () => {
  const a = { ...node, id: 'a', node_id: 'la', parent_id: 'root', title: 'A', x: 350, y: 40, sort_order: 1 };
  const grand = { ...node, id: 'grand', node_id: 'lg', parent_id: 'a', title: 'Grand', x: 680, y: 40, sort_order: 1 };
  const b = { ...a, id: 'b', node_id: 'lb', title: 'B', y: 300, sort_order: 2 };
  const dest = { ...a, id: 'dest', node_id: 'ld', title: 'Dest', y: 600, sort_order: 3 };
  beforeEach(() => {
    class TestPointerEvent extends MouseEvent { pointerId: number; constructor(type: string, options: PointerEventInit = {}) { super(type, options); this.pointerId = options.pointerId ?? 1; } }
    vi.stubGlobal('PointerEvent', TestPointerEvent);
    f.api.scene.mockResolvedValue({ ...structuredClone(initial), nodes: [node, a, grand, b, dest] });
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(() => ({ left: 0, top: 0, right: 1200, bottom: 1000, width: 1200, height: 1000, x: 0, y: 0, toJSON: () => ({}) }));
  });
  afterEach(() => vi.restoreAllMocks());
  function pick(name: string, modifiers = {}) { fireEvent.pointerDown(screen.getByRole('article', { name, exact: true }), { button: 0, pointerId: 1, ...modifiers }); fireEvent.pointerUp(screen.getByLabelText('知识画布'), { pointerId: 1 }); }
  it('retains selected roots while dragging and sends all folded descendants once', async () => {
    show(); await ready(); pick('A'); pick('B', { ctrlKey: true });
    fireEvent.click(screen.getByRole('button', { name: '折叠或展开 A' }));
    const canvas = screen.getByLabelText('知识画布');
    fireEvent.pointerDown(screen.getByRole('article', { name: 'A', exact: true }), { button: 0, pointerId: 1, clientX: 430, clientY: 160 });
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 1100, clientY: 900 });
    expect(screen.getByText('已选 2 个节点（含后代 3 个）')).toBeInTheDocument();
    fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 1100, clientY: 900 });
    await waitFor(() => expect(f.api.command).toHaveBeenCalledWith('move_branches', expect.objectContaining({ placement: 'free', objects: [{ object_id: 'a', expected_revision: 1 }, { object_id: 'b', expected_revision: 1 }], nodes: expect.arrayContaining([expect.objectContaining({ node_id: 'lg' })]) }), expect.any(String)));
    expect(f.api.command.mock.calls[0][1].nodes).toHaveLength(3);
  });
  it('Esc cancels group preview and restores positions without a mutation', async () => {
    show(); await ready(); pick('A'); pick('B', { metaKey: true }); const canvas = screen.getByLabelText('知识画布');
    fireEvent.pointerDown(screen.getByRole('article', { name: 'A', exact: true }), { button: 0, pointerId: 1, clientX: 430, clientY: 160 });
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 1100, clientY: 900 }); fireEvent.keyDown(canvas, { key: 'Escape' });
    fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 1100, clientY: 900 });
    expect(f.api.command).not.toHaveBeenCalled(); expect(screen.getByRole('article', { name: 'A', exact: true })).toHaveStyle({ left: '350px', top: '40px' });
  });
  it.each(['metaKey','ctrlKey'])('%s+X marks a whole branch then pastes with original IDs', async modifier => {
    show(); await ready(); pick('A'); pick('B', { [modifier]: true });
    fireEvent.keyDown(screen.getByLabelText('知识画布'), { key: 'x', [modifier]: true });
    expect(f.api.command).not.toHaveBeenCalled(); expect(document.querySelectorAll('.kc-cut-source')).toHaveLength(3);
    pick('Dest'); fireEvent.click(screen.getByRole('button', { name: '粘贴分支', exact: true }));
    await waitFor(() => expect(f.api.command).toHaveBeenCalledWith('move_branches', expect.objectContaining({ placement: 'child', target_id: 'dest', objects: expect.arrayContaining([{ object_id: 'a', expected_revision: 1 }, { object_id: 'grand', expected_revision: 1 }, { object_id: 'b', expected_revision: 1 }]) }), expect.any(String)));
    await waitFor(() => expect(screen.getByRole('button', { name: '粘贴分支', exact: true })).toBeDisabled());
  });
  it('cancels a pending cut with Esc and refuses its old system clipboard token', async () => {
    show(); await ready(); pick('A'); const values = new Map<string,string>(); const transfer = { setData: (k:string,v:string) => values.set(k,v), getData: (k:string) => values.get(k) ?? '' };
    fireEvent.cut(screen.getByLabelText('知识画布'), { clipboardData: transfer });
    fireEvent.keyDown(screen.getByLabelText('知识画布'), { key: 'Escape' });
    expect(document.querySelector('.kc-cut-source')).toBeNull();
    fireEvent.paste(screen.getByLabelText('知识画布'), { clipboardData: transfer });
    expect(await screen.findByRole('alert')).toHaveTextContent('剪切已完成或取消'); expect(f.api.command).not.toHaveBeenCalled();
  });
  it('keeps the cut source and marker if the destination save conflicts', async () => {
    show(); await ready(); pick('A'); fireEvent.click(screen.getByRole('button', { name: '剪切分支', exact: true })); pick('Dest');
    f.api.command.mockRejectedValue(Object.assign(new Error('内容已更新'), { status: 409 }));
    fireEvent.click(screen.getByRole('button', { name: '粘贴分支', exact: true }));
    await screen.findByText(/内容已更新/); expect(screen.getByRole('article', { name: 'A', exact: true })).toHaveClass('kc-cut-source');
    expect(screen.getByRole('article', { name: 'Grand', exact: true })).toBeInTheDocument();
  });
});

describe('方向键与分支折叠', () => {
  const a={...node,id:'a',node_id:'la',parent_id:'root',title:'A',x:350,y:40,sort_order:1};
  const grand={...node,id:'grand',node_id:'lg',parent_id:'a',title:'Grand',x:680,y:40,sort_order:1};
  const b={...a,id:'b',node_id:'lb',title:'B',y:300,sort_order:2};
  beforeEach(() => { f.api.scene.mockResolvedValue({...structuredClone(initial),nodes:[node,a,grand,b]}); });
  async function start() { show(); await ready(); fireEvent.pointerDown(screen.getByRole('article',{name:'项目总纲'}),{button:0,pointerId:1});fireEvent.pointerUp(screen.getByLabelText('知识画布'),{pointerId:1});return screen.getByLabelText('知识画布'); }
  it('navigates siblings and children and collapses before returning to parent', async () => {
    const canvas=await start();fireEvent.keyDown(canvas,{key:'ArrowRight'});expect(screen.getByRole('article',{name:'A',exact:true})).toHaveClass('selected');
    fireEvent.keyDown(canvas,{key:'ArrowDown'});expect(screen.getByRole('article',{name:'B',exact:true})).toHaveClass('selected');
    fireEvent.keyDown(canvas,{key:'ArrowUp'});fireEvent.keyDown(canvas,{key:'ArrowLeft'});expect(screen.queryByRole('article',{name:'Grand'})).toBeNull();
    fireEvent.keyDown(canvas,{key:'ArrowRight'});expect(screen.getByRole('article',{name:'Grand'})).toBeInTheDocument();expect(screen.getByRole('article',{name:'A',exact:true})).toHaveClass('selected');
    fireEvent.keyDown(canvas,{key:'ArrowRight'});expect(screen.getByRole('article',{name:'Grand'})).toHaveClass('selected');expect(f.api.command).not.toHaveBeenCalled();
  });
  it.each(['metaKey','ctrlKey'])('uses %s+/ without modifying data or native input',async modifier=>{
    const canvas=await start();fireEvent.keyDown(canvas,{key:'/',[modifier]:true});expect(screen.queryByRole('article',{name:'A',exact:true})).toBeNull();
    fireEvent.keyDown(canvas,{key:'/',[modifier]:true});expect(screen.getByRole('article',{name:'A',exact:true})).toBeInTheDocument();
    fireEvent.keyDown(canvas,{key:'F2'});const input=screen.getByLabelText('节点标题');fireEvent.keyDown(input,{key:'ArrowRight'});fireEvent.keyDown(input,{key:'/',[modifier]:true});expect(input).toBeInTheDocument();expect(f.api.command).not.toHaveBeenCalled();
  });
  it('moves hidden selection to collapsed ancestor and exposes expanded state',async()=>{
    const canvas=await start();fireEvent.keyDown(canvas,{key:'ArrowRight'});fireEvent.keyDown(canvas,{key:'ArrowRight'});
    fireEvent.click(screen.getByRole('button',{name:'折叠或展开 项目总纲'}));
    expect(screen.getByRole('article',{name:'项目总纲'})).toHaveClass('selected');expect(screen.getByRole('button',{name:'折叠或展开 项目总纲'})).toHaveAttribute('aria-expanded','false');
  });
});

describe('空间、相对插入、右键落点和真实框选', () => {
  beforeEach(() => {
    class TestPointerEvent extends MouseEvent { pointerId: number; constructor(type: string, options: PointerEventInit = {}) { super(type,options);this.pointerId=options.pointerId??1; } }
    vi.stubGlobal('PointerEvent',TestPointerEvent);
  });
  function pickRoot() { fireEvent.pointerDown(screen.getByRole('article',{name:'项目总纲'}),{button:0,pointerId:1});fireEvent.pointerUp(screen.getByLabelText('知识画布'),{pointerId:1}); }
  it('shows one space and clears old targets on switching before selecting all',async()=>{
    const other={...node,id:'personal-node',container_id:'personal',node_id:'other-layout',title:'个人节点'};
    f.api.scene.mockResolvedValue({...structuredClone(initial),nodes:[node,other]});show();await ready();
    expect(screen.queryByRole('article',{name:'个人节点'})).toBeNull();pickRoot();
    fireEvent.click(screen.getByRole('tab',{name:'个人知识库',exact:true}));expect(screen.queryByRole('article',{name:'项目总纲'})).toBeNull();
    expect(screen.getByRole('button',{name:'删除分支',exact:true})).toBeDisabled();fireEvent.keyDown(screen.getByLabelText('知识画布'),{key:'a',metaKey:true});
    fireEvent.click(screen.getByRole('button',{name:'删除分支',exact:true}));await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('delete_branches',expect.objectContaining({container_id:'personal',objects:[{object_id:'personal-node',expected_revision:1}]}),expect.any(String)));
  });
  it('creates Enter sibling with explicit anchor and one layout payload',async()=>{
    const b={...node,id:'b',node_id:'lb',title:'B',y:250};f.api.scene.mockResolvedValue({...structuredClone(initial),nodes:[node,b]});show();await ready();pickRoot();
    fireEvent.keyDown(screen.getByLabelText('知识画布'),{key:'Enter'});fireEvent.change(screen.getByLabelText('新主题标题'),{target:{value:'插入节点'}});fireEvent.keyDown(screen.getByLabelText('新主题标题'),{key:'Enter'});
    await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('create_object',expect.objectContaining({after_object_id:'root',expected_container_revision:12,parent_id:null,nodes:expect.arrayContaining([expect.objectContaining({node_id:'lb'})])}),expect.any(String)));
  });
  it('right blank paste ignores old selection and anchors at click in world coordinates',async()=>{
    show();await ready();pickRoot();fireEvent.click(screen.getByRole('button',{name:'复制分支',exact:true}));await snapshotSettled();
    fireEvent.contextMenu(screen.getByLabelText('知识画布'),{clientX:900,clientY:500});fireEvent.click(screen.getByRole('menuitem',{name:'粘贴分支 · ⌘V / Ctrl+V'}));
    await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('copy_branches',expect.objectContaining({parent_id:null,offset:{x:820,y:365}}),expect.any(String)));
  });
  it('right node paste uses the clicked parent and a separate child location',async()=>{
    const target={...node,id:'target',node_id:'lt',title:'目标',x:500};f.api.scene.mockResolvedValue({...structuredClone(initial),nodes:[node,target]});show();await ready();pickRoot();fireEvent.click(screen.getByRole('button',{name:'复制分支',exact:true}));await snapshotSettled();
    fireEvent.contextMenu(screen.getByRole('article',{name:'目标'}),{clientX:700,clientY:300});fireEvent.click(screen.getByRole('menuitem',{name:'粘贴分支 · ⌘V / Ctrl+V'}));
    await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('copy_branches',expect.objectContaining({parent_id:'target',offset:{x:810,y:0}}),expect.any(String)));
  });
  it('selects the lower area of a tall card using measured dimensions',async()=>{
    show();await ready();const article=screen.getByRole('article',{name:'项目总纲'});Object.defineProperty(article,'offsetHeight',{value:400,configurable:true});Object.defineProperty(article,'offsetWidth',{value:300,configurable:true});
    const canvas=screen.getByLabelText('知识画布');fireEvent.pointerDown(canvas,{button:0,pointerId:1,clientX:100,clientY:400});fireEvent.pointerMove(canvas,{pointerId:1,clientX:200,clientY:450});fireEvent.pointerUp(canvas,{pointerId:1,clientX:200,clientY:450});expect(article).toHaveClass('selected');
  });
  it('does not avoid invisible nodes from another space when creating in blank active space',async()=>{
    show();await ready();fireEvent.click(screen.getByRole('tab',{name:'个人知识库',exact:true}));
    fireEvent.doubleClick(screen.getByLabelText('知识画布'),{clientX:100,clientY:150});expect(document.querySelector('.kc-quick')).toHaveStyle({left:'40px',top:'55px'});
  });
});

it('names and activates the target space before a cross-space history action',async()=>{
  f.api.scene.mockResolvedValue({...structuredClone(initial),nodes:[node,{...node,id:'mine',container_id:'personal',title:'个人节点'}]});
  f.api.history.mockResolvedValue({undo:{history_id:'other-step',operation:'update_title',container_id:'personal',container_title:'个人知识库',container_kind:'personal',can_apply:true},redo:null});
  show();await ready();fireEvent.click(screen.getByRole('button',{name:'撤销 「个人知识库」改名'}));
  await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('undo_canvas',{history_id:'other-step'},expect.any(String)));
  expect(screen.getByRole('article',{name:'个人节点'})).toBeInTheDocument();expect(screen.queryByRole('article',{name:'项目总纲'})).toBeNull();
});

it('keeps paste focus on newly created nodes rather than existing nodes in another space',async()=>{
 const other={...node,id:'other',container_id:'personal',title:'其他空间已有节点'};let state={...structuredClone(initial),nodes:[node,other]};f.api.scene.mockImplementation(async()=>structuredClone(state));
 f.api.command.mockImplementation(async(operation)=>{if(operation==='copy_branches')state={...state,nodes:[...state.nodes,{...node,id:'copy',title:'粘贴结果',x:800,y:400}]};return {success:true,data:{object_id:null}};});
 show();await ready();fireEvent.pointerDown(screen.getByRole('article',{name:'项目总纲'}),{button:0,pointerId:1});fireEvent.pointerUp(screen.getByLabelText('知识画布'),{pointerId:1});fireEvent.click(screen.getByRole('button',{name:'复制分支',exact:true}));await snapshotSettled();fireEvent.click(screen.getByRole('button',{name:'粘贴分支',exact:true}));
 await waitFor(()=>expect(screen.getByRole('article',{name:'粘贴结果'})).toHaveClass('selected'));expect(screen.queryByRole('article',{name:'其他空间已有节点'})).toBeNull();
});

it('switches between projects of the same kind and refuses clipboard from another project',async()=>{
 const second={id:'p2',kind:'project',title:'第二项目',role:'manager',revision:1};f.api.scene.mockResolvedValue({...structuredClone(initial),containers:[...initial.containers,second],nodes:[node,{...node,id:'p2-node',node_id:'l2',container_id:'p2',title:'第二项目节点'}]});
 show();await ready();fireEvent.pointerDown(screen.getByRole('article',{name:'项目总纲'}),{button:0,pointerId:1});fireEvent.pointerUp(screen.getByLabelText('知识画布'),{pointerId:1});fireEvent.click(screen.getByRole('button',{name:'复制分支',exact:true}));await snapshotSettled();fireEvent.click(screen.getByRole('button',{name:'第二项目 可管理'}));
 expect(screen.queryByRole('article',{name:'项目总纲'})).toBeNull();expect(screen.getByRole('article',{name:'第二项目节点'})).toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'粘贴分支',exact:true}));expect(await screen.findByRole('alert')).toHaveTextContent('原知识空间');expect(f.api.command).not.toHaveBeenCalled();
});

describe('滚轮、边缘平移、悬停展开和视野',()=>{
 let frames:Map<number,FrameRequestCallback>,counter:number;
 const a={...node,id:'a',node_id:'la',parent_id:'root',title:'A',x:350,y:40,sort_order:1};
 const b={...a,id:'b',node_id:'lb',title:'B',y:300,sort_order:2};
 const child={...a,id:'child',node_id:'lc',title:'B内部',parent_id:'b',x:680,y:300};
 beforeEach(()=>{
  frames=new Map();counter=0;vi.stubGlobal('requestAnimationFrame',(cb:FrameRequestCallback)=>{frames.set(++counter,cb);return counter;});vi.stubGlobal('cancelAnimationFrame',(id:number)=>frames.delete(id));
  class TestPointerEvent extends MouseEvent {pointerId:number;constructor(type:string,options:PointerEventInit={}){super(type,options);this.pointerId=options.pointerId??1;}}vi.stubGlobal('PointerEvent',TestPointerEvent);
  f.api.scene.mockResolvedValue({...structuredClone(initial),nodes:[node,a,b,child]});
 });
 function frame(time:number){const callbacks=[...frames.values()];frames.clear();act(()=>callbacks.forEach(cb=>cb(time)));}
 async function start(){show();await ready();const canvas=screen.getByLabelText('知识画布');canvas.getBoundingClientRect=()=>({left:0,top:0,right:1200,bottom:1000,width:1200,height:1000,x:0,y:0,toJSON:()=>({})});return canvas;}
 function pick(name:string,mods={}){fireEvent.pointerDown(screen.getByRole('article',{name,exact:true}),{button:0,pointerId:1,...mods});fireEvent.pointerUp(screen.getByLabelText('知识画布'),{pointerId:1});}
 it('plain wheel pans, modified wheel zooms and reset returns to 100% without data commands',async()=>{
  const canvas=await start();const world=document.querySelector('.kc-world')!;
  fireEvent.wheel(canvas,{deltaX:30,deltaY:40,clientX:400,clientY:300});expect(world.getAttribute('style')).toContain('translate(30px,55px) scale(1)');
  fireEvent.wheel(canvas,{deltaY:-100,ctrlKey:true,clientX:400,clientY:300});expect(world.getAttribute('style')).not.toContain('scale(1)');
  fireEvent.click(screen.getByRole('button',{name:'回到100%'}));expect(world.getAttribute('style')).toContain('scale(1)');expect(f.api.command).not.toHaveBeenCalled();
 });
 it.each([false,true])('edge pan persists with stationary pointer and saves correct world coordinates (multi=%s)',async multi=>{
  const canvas=await start();pick('A');if(multi)pick('B',{ctrlKey:true});
  fireEvent.pointerDown(screen.getByRole('article',{name:'A',exact:true}),{button:0,pointerId:1,clientX:430,clientY:160});fireEvent.pointerMove(canvas,{pointerId:1,clientX:1195,clientY:800});
  frame(0);frame(16);frame(32);const moved=screen.getByRole('article',{name:'A',exact:true});const x=parseFloat(moved.style.left);expect(x).toBeGreaterThan(350+765);
  fireEvent.pointerUp(canvas,{pointerId:1,clientX:1195,clientY:800});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith(multi?'move_branches':'move_branch',expect.objectContaining({placement:'free',nodes:expect.arrayContaining([expect.objectContaining({node_id:'la',position:expect.objectContaining({x})})])}),expect.any(String)));
  expect(frames.size).toBe(0);
 });
 it('marquee continues in world space at edge and cancels without data mutation',async()=>{
  const canvas=await start();fireEvent.pointerDown(canvas,{button:0,pointerId:1,clientX:50,clientY:150});fireEvent.pointerMove(canvas,{pointerId:1,clientX:1195,clientY:800});
  const before=parseFloat((document.querySelector('.kc-marquee') as HTMLElement).style.width);frame(0);frame(16);frame(32);expect(parseFloat((document.querySelector('.kc-marquee') as HTMLElement).style.width)).toBeGreaterThan(before);
  fireEvent.keyDown(canvas,{key:'Escape'});expect(document.querySelector('.kc-marquee')).toBeNull();expect(frames.size).toBe(0);expect(f.api.command).not.toHaveBeenCalled();
 });
 it('hover unfolds a legal folded target and Esc restores its original fold',async()=>{
  const canvas=await start();fireEvent.click(screen.getByRole('button',{name:'折叠或展开 B'}));expect(screen.queryByRole('article',{name:'B内部'})).toBeNull();pick('A');
  fireEvent.pointerDown(screen.getByRole('article',{name:'A',exact:true}),{button:0,pointerId:1,clientX:430,clientY:160});fireEvent.pointerMove(canvas,{pointerId:1,clientX:460,clientY:455});
  frame(performance.now()+700);expect(screen.getByRole('article',{name:'B内部'})).toBeInTheDocument();fireEvent.keyDown(canvas,{key:'Escape'});expect(screen.queryByRole('article',{name:'B内部'})).toBeNull();expect(f.api.command).not.toHaveBeenCalled();
 });
 it('fit all uses only visible active-space cards and does not produce history',async()=>{
  const canvas=await start();fireEvent.click(screen.getByRole('button',{name:'适配全图'}));const world=document.querySelector('.kc-world')!;expect(world.getAttribute('style')).not.toContain('translate(60px,95px)');
  fireEvent.click(screen.getByRole('button',{name:'回到100%'}));expect(world.getAttribute('style')).toContain('scale(1)');expect(canvas).toBeInTheDocument();expect(f.api.command).not.toHaveBeenCalled();
 });
 it('remeasures a long child after hover expansion before choosing its lower-half drop target',async()=>{
  const size=vi.spyOn(HTMLElement.prototype,'offsetHeight','get').mockImplementation(function(this:HTMLElement){return this.getAttribute('aria-label')==='B内部'?400:170;});
  try {
   const canvas=await start();fireEvent.click(screen.getByRole('button',{name:'折叠或展开 B'}));pick('A');
   fireEvent.pointerDown(screen.getByRole('article',{name:'A',exact:true}),{button:0,pointerId:1,clientX:430,clientY:160});fireEvent.pointerMove(canvas,{pointerId:1,clientX:460,clientY:455});frame(performance.now()+700);
   fireEvent.pointerMove(canvas,{pointerId:1,clientX:800,clientY:695});fireEvent.pointerUp(canvas,{pointerId:1,clientX:800,clientY:695});
   await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('move_branch',expect.objectContaining({target_id:'child',placement:'child'}),expect.any(String)));
  } finally {size.mockRestore();}
 });
 it('discards old identity drag state instead of restoring or committing its scene',async()=>{
  const rendered=show();await ready();const canvas=screen.getByLabelText('知识画布');canvas.getBoundingClientRect=()=>({left:0,top:0,right:1200,bottom:1000,width:1200,height:1000,x:0,y:0,toJSON:()=>({})});pick('A');
  fireEvent.pointerDown(screen.getByRole('article',{name:'A',exact:true}),{button:0,pointerId:1,clientX:430,clientY:160});fireEvent.pointerMove(canvas,{pointerId:1,clientX:1195,clientY:800});frame(0);frame(16);
  f.auth.currentOrgId='different-org';f.api.scene.mockResolvedValue({...structuredClone(initial),nodes:[{...node,id:'new-identity',title:'新身份节点'}]});rendered.rerender(<MemoryRouter><KnowledgeCanvas/></MemoryRouter>);
  await screen.findByRole('article',{name:'新身份节点'});fireEvent.pointerUp(screen.getByLabelText('知识画布'),{pointerId:1,clientX:1195,clientY:800});
  expect(screen.queryByRole('article',{name:'A',exact:true})).toBeNull();expect(f.api.command).not.toHaveBeenCalled();expect(frames.size).toBe(0);
 });

});

describe('草稿保护与刷新后的剪切安全', () => {
  it('recovers the unsaved body after remount, without issuing a save', async () => {
    const view = show(); await ready(); fireEvent.click(screen.getByRole('button', { name: '展开编辑' }));
    fireEvent.change(await screen.findByLabelText('正文'), { target: { value: '离页保留的草稿' } });
    await waitFor(() => expect(Object.values(localStorage).join('')).toContain('离页保留的草稿'));
    const event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event); expect(event.defaultPrevented).toBe(true);
    view.unmount(); show(); await ready(); fireEvent.click(await screen.findByRole('button', { name: '恢复草稿' }));
    expect(await screen.findByLabelText('正文')).toHaveValue('离页保留的草稿');
    expect(f.api.command).not.toHaveBeenCalled();
  });
  it('keeps the original expected revision when restoring a stale body', async () => {
    const view = show(); await ready(); fireEvent.click(screen.getByRole('button', { name: '展开编辑' }));
    fireEvent.change(await screen.findByLabelText('正文'), { target: { value: '我的旧版本草稿' } }); view.unmount();
    f.api.object.mockResolvedValue({ ...node, revision: 5, body: '别人的新正文', can_write: true });
    show(); await ready(); fireEvent.click(await screen.findByRole('button', { name: '恢复草稿' }));
    expect(await screen.findByLabelText('正文')).toHaveValue('我的旧版本草稿');
    fireEvent.click(screen.getByRole('button', { name: '保存正文' }));
    await waitFor(() => expect(f.api.command).toHaveBeenCalledWith('save_document', expect.objectContaining({ expected_revision: 1 }), expect.any(String)));
  });
  it('checks current write permission before revealing a recovered body', async () => {
    const view = show(); await ready(); fireEvent.click(screen.getByRole('button', { name: '展开编辑' }));
    fireEvent.change(await screen.findByLabelText('正文'), { target: { value: '不可越权恢复' } }); view.unmount();
    f.api.object.mockResolvedValue({ ...node, body: '', can_write: false });
    show(); await ready(); fireEvent.click(await screen.findByRole('button', { name: '恢复草稿' }));
    expect((await screen.findAllByRole('alert')).some(e => e.textContent?.includes('已无编辑权限'))).toBe(true);
    expect(screen.queryByLabelText('正文')).not.toBeInTheDocument();
  });
  it('isolates recovery from a different organization', async () => {
    const view = show(); await ready(); fireEvent.click(screen.getByRole('button', { name: '展开编辑' }));
    fireEvent.change(await screen.findByLabelText('正文'), { target: { value: '企业一的草稿' } }); view.unmount();
    f.auth.currentOrgId = 'another-org'; show(); await ready();
    expect(screen.queryByRole('button', { name: '恢复草稿' })).not.toBeInTheDocument();
  });
  it('reuses the original command ID after refreshing an uncertain save', async () => {
    const view = show(); await ready(); fireEvent.click(screen.getByRole('button', { name: '展开编辑' }));
    fireEvent.change(await screen.findByLabelText('正文'), { target: { value: '未知结果' } });
    f.api.command.mockRejectedValueOnce(new Error('network')); fireEvent.click(screen.getByRole('button', { name: '保存正文' }));
    await screen.findAllByRole('alert'); const commandId = f.api.command.mock.calls[0][2]; view.unmount();
    show(); await ready(); fireEvent.click(await screen.findByRole('button', { name: '恢复草稿' }));
    await screen.findByLabelText('正文'); fireEvent.click(screen.getByRole('button', { name: '保存正文' }));
    await waitFor(() => expect(f.api.command).toHaveBeenLastCalledWith('save_document', expect.objectContaining({ body: '未知结果' }), commandId));
  });
  it('rejects the old cut token after cancel and remount', async () => {
    const view = show(); await ready(); const root = screen.getByRole('article', { name: '项目总纲' });
    fireEvent.contextMenu(root, { clientX: 100, clientY: 100 }); fireEvent.click(screen.getByRole('menuitem', { name: '关闭菜单' }));
    let record = ''; const clipboardData = { setData: (mime: string, value: string) => { if (mime === 'application/x-everydayai-branches') record = value; } };
    fireEvent.cut(screen.getByLabelText('知识画布'), { clipboardData }); expect(record).toContain('cut');
    fireEvent.keyDown(screen.getByLabelText('知识画布'), { key: 'Escape' }); view.unmount();
    show(); await ready(); fireEvent.paste(screen.getByLabelText('知识画布'), { clipboardData: { getData: () => record } });
    expect(await screen.findByRole('alert')).toHaveTextContent('页面刷新后需重新剪切'); expect(f.api.command).not.toHaveBeenCalled();
  });
});

it('recovers an uncertain canvas-only operation and checks it with the original command ID', async () => {
  const view = show(); await ready();
  fireEvent.contextMenu(screen.getByRole('article', { name: '项目总纲' }), { clientX: 100, clientY: 100 }); fireEvent.click(screen.getByRole('menuitem', { name: '关闭菜单' }));
  f.api.command.mockRejectedValueOnce(new Error('network'));
  fireEvent.click(screen.getByRole('button', { name: '删除分支', exact: true }));
  await screen.findByRole('alert'); const commandId = f.api.command.mock.calls[0][2]; view.unmount();
  show(); await ready(); fireEvent.click(await screen.findByRole('button', { name: '恢复草稿' }));
  await waitFor(() => expect(screen.queryByRole('dialog', { name: '恢复未保存草稿' })).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', { name: '刷新', exact: true }));
  await waitFor(() => expect(f.api.command).toHaveBeenLastCalledWith('delete_branches', expect.any(Object), commandId));
});
it('selects the restored branch after undo and surviving parent after redo', async () => {
  const child = { ...node, id: 'child', node_id: 'child-placement', parent_id: 'root', title: '待恢复分支', x: 1000 };
  const state = structuredClone(initial);
  f.api.scene.mockImplementation(async () => structuredClone(state));
  f.api.history.mockResolvedValue({ undo: { history_id: 'h', container_id: 'p', operation: 'delete_branches', can_apply: true }, redo: { history_id: 'h', container_id: 'p', operation: 'delete_branches', can_apply: true } });
  f.api.command.mockImplementation(async (op) => { if (op === 'undo_canvas') state.nodes.push(child); else if (op === 'redo_canvas') state.nodes = [node]; return { success: true, data: { object_id: null } }; });
  show(); await ready(); fireEvent.click(screen.getByRole('button', { name: /^撤销/ }));
  await waitFor(() => expect(screen.getByRole('article', { name: '待恢复分支' })).toHaveClass('selected'));
  fireEvent.click(screen.getByRole('button', { name: /^重做/ }));
  await waitFor(() => expect(screen.queryByRole('article', { name: '待恢复分支' })).not.toBeInTheDocument());
  expect(screen.getByRole('article', { name: '项目总纲' })).toHaveClass('selected');
});
it('recovers inline creation after refresh without automatically creating a node', async () => {
  const view = show(); await ready(); fireEvent.doubleClick(screen.getByLabelText('知识画布'), { clientX: 250, clientY: 250 });
  fireEvent.change(screen.getByLabelText('新主题标题'), { target: { value: '还未按回车' } }); view.unmount();
  show(); await ready(); expect(await screen.findByRole('dialog', { name: '恢复未保存草稿' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '恢复草稿' }));
  expect(await screen.findByLabelText('新主题标题')).toHaveValue('还未按回车'); expect(f.api.command).not.toHaveBeenCalled();
});

it('preserves drafts when switching organization away and back without unmounting', async () => {
  const view = show(); await ready(); fireEvent.click(screen.getByRole('button', { name: '展开编辑' }));
  fireEvent.change(await screen.findByLabelText('正文'), { target: { value: '返回原企业后恢复' } });
  f.auth.currentOrgId = 'other-org'; view.rerender(<MemoryRouter><KnowledgeCanvas /></MemoryRouter>); await ready();
  expect(screen.queryByRole('button', { name: '恢复草稿' })).not.toBeInTheDocument();
  f.auth.currentOrgId = 'org'; view.rerender(<MemoryRouter><KnowledgeCanvas /></MemoryRouter>); await ready();
  fireEvent.click(await screen.findByRole('button', { name: '恢复草稿' }));
  expect(await screen.findByLabelText('正文')).toHaveValue('返回原企业后恢复');
});
it('rechecks container permission at restore time instead of using the loaded scene', async () => {
  const view = show(); await ready(); fireEvent.click(screen.getByRole('button', { name: '展开编辑' }));
  fireEvent.change(await screen.findByLabelText('正文'), { target: { value: '不可恢复的草稿' } }); view.unmount(); sessionStorage.clear();
  show(); await ready(); await screen.findByRole('button', { name: '恢复草稿' });
  f.api.scene.mockResolvedValue({ ...structuredClone(initial), containers: [{ ...initial.containers[0], role: 'viewer' }] });
  fireEvent.click(screen.getByRole('button', { name: '恢复草稿' }));
  await waitFor(() => expect(screen.getAllByRole('alert').some(e => e.textContent?.includes('无编辑权限'))).toBe(true));
  expect(screen.queryByLabelText('正文')).not.toBeInTheDocument();
});

describe('复制内容快照和历史限制', () => {
  it('captures on copy, then pastes the snapshot after source deletion with current space revision', async () => {
    let state = structuredClone(initial); f.api.scene.mockImplementation(async () => structuredClone(state));
    show(); await ready(); fireEvent.contextMenu(screen.getByRole('article', { name: '项目总纲' }), { clientX: 200, clientY: 200 }); fireEvent.click(screen.getByRole('menuitem', { name: '关闭菜单' }));
    const transfer = { setData: vi.fn() }; fireEvent.copy(screen.getByLabelText('知识画布'), { clipboardData: transfer });
    const record = JSON.parse(transfer.setData.mock.calls.find(c => c[0] === 'application/x-everydayai-branches')![1]);
    await snapshotSettled(); expect(f.api.captureSnapshot).toHaveBeenCalledWith(expect.objectContaining({ snapshot_id: record.snapshot_id, expected_container_revision: 12 }));
    expect(record.snapshot_nodes[0]).toMatchObject({ title: '项目总纲', x: 20, y: 40 });
    state = { ...state, containers: [{ ...initial.containers[0], revision: 20 }], nodes: [] };
    fireEvent.click(screen.getByRole('button', { name: '刷新', exact: true })); await waitFor(() => expect(screen.queryByRole('article')).not.toBeInTheDocument());
    fireEvent.paste(screen.getByLabelText('知识画布'), { clipboardData: { getData: () => JSON.stringify(record) } });
    await waitFor(() => expect(f.api.command).toHaveBeenCalledWith('copy_branches', expect.objectContaining({ snapshot_id: record.snapshot_id, expected_container_revision: 20, offset: { x: 40, y: -40 } }), expect.any(String)));
  });
  it('waits for copy-time capture before accepting immediate native paste', async () => {
    let finish!: () => void; f.api.captureSnapshot.mockReturnValue(new Promise<void>(resolve => { finish = resolve; }));
    show(); await ready(); fireEvent.contextMenu(screen.getByRole('article', { name: '项目总纲' }), { clientX: 100, clientY: 100 }); fireEvent.click(screen.getByRole('menuitem', { name: '关闭菜单' }));
    const transfer = { setData: vi.fn() }; fireEvent.copy(screen.getByLabelText('知识画布'), { clipboardData: transfer });
    const record = transfer.setData.mock.calls.find(c => c[0] === 'application/x-everydayai-branches')![1];
    fireEvent.paste(screen.getByLabelText('知识画布'), { clipboardData: { getData: () => record } }); expect(f.api.command).not.toHaveBeenCalled();
    await act(async () => finish()); await waitFor(() => expect(f.api.command).toHaveBeenCalledOnce());
  });
  it('does not fall back to live content when capturing fails', async () => {
    f.api.captureSnapshot.mockRejectedValue(new Error('源内容已变化'));
    show(); await ready(); fireEvent.contextMenu(screen.getByRole('article', { name: '项目总纲' }), { clientX: 100, clientY: 100 }); fireEvent.click(screen.getByRole('menuitem', { name: '关闭菜单' }));
    const transfer = { setData: vi.fn() }; fireEvent.copy(screen.getByLabelText('知识画布'), { clipboardData: transfer });
    const record = transfer.setData.mock.calls.find(c => c[0] === 'application/x-everydayai-branches')![1];
    expect(await screen.findByRole('alert')).toHaveTextContent('复制快照未完成');
    fireEvent.paste(screen.getByLabelText('知识画布'), { clipboardData: { getData: () => record } }); await act(async () => {}); expect(f.api.command).not.toHaveBeenCalled();
  });
  it.each([{key:'z',metaKey:true}, {key:'y',ctrlKey:true}])('explains unavailable history for $key on canvas', async keys => {
    show(); await ready(); fireEvent.keyDown(screen.getByLabelText('知识画布'), keys);
    expect(screen.getAllByRole('status').some(n => n.textContent?.includes('暂无可'))).toBe(true);
    fireEvent.click(screen.getByText('撤销重做范围与限制'));
    expect(screen.getByText(/保留最近 100 步/)).toHaveTextContent('正文保存、文件更新和成员权限不进入画布撤销');
    expect(f.api.command).not.toHaveBeenCalled();
  });
  it('explains permission and external-write barriers without enabling unsafe history', async () => {
    f.api.history.mockResolvedValue({ undo: { history_id: 'h', can_apply: false, operation: 'update_title', reason: '当前没有编辑权限，无法撤销或重做此操作' }, redo: null });
    show(); await ready(); const button = screen.getByRole('button', { name: '撤销 改名' }); expect(button).toBeDisabled(); expect(button).toHaveAttribute('title', expect.stringContaining('没有编辑权限'));
    fireEvent.keyDown(screen.getByLabelText('知识画布'), { key: 'z', ctrlKey: true }); expect(screen.getAllByRole('status').some(n => n.textContent?.includes('没有编辑权限'))).toBe(true);
  });
});

it('uses the same copy snapshot token from the system clipboard after remount', async () => {
  const view = show(); await ready(); fireEvent.contextMenu(screen.getByRole('article', { name: '项目总纲' }), { clientX: 100, clientY: 100 }); fireEvent.click(screen.getByRole('menuitem', { name: '关闭菜单' }));
  const transfer = { setData: vi.fn() }; fireEvent.copy(screen.getByLabelText('知识画布'), { clipboardData: transfer }); await snapshotSettled();
  const data = transfer.setData.mock.calls.find(c => c[0] === 'application/x-everydayai-branches')![1]; const token = JSON.parse(data).snapshot_id;
  view.unmount(); show(); await ready(); fireEvent.paste(screen.getByLabelText('知识画布'), { clipboardData: { getData: () => data } });
  await waitFor(() => expect(f.api.command).toHaveBeenCalledWith('copy_branches', expect.objectContaining({ snapshot_id: token }), expect.any(String)));
  expect(f.api.captureSnapshot).toHaveBeenCalledOnce();
});
it('rejects malformed clipboard snapshot geometry without issuing a mutation', async () => {
  show(); await ready(); fireEvent.paste(screen.getByLabelText('知识画布'), { clipboardData: { getData: () => JSON.stringify({ identity: 'u:org', container: 'p', mode: 'copy', objects: [], snapshot_id: 'token', snapshot_nodes: 'invalid' }) } });
  expect(await screen.findByRole('alert')).toHaveTextContent('请重新复制'); expect(f.api.command).not.toHaveBeenCalled();
});

describe('P1 save and readonly regressions', () => {
  it('retains body typed during save and uses the acknowledged revision for the next save', async () => {
    let finish!: (v: unknown) => void;
    f.api.command.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    show(); await ready(); fireEvent.click(screen.getByRole('button', {name:'展开编辑'}));
    const dialog = await screen.findByRole('dialog', {name:'编辑内容'});
    fireEvent.change(within(dialog).getByLabelText('正文'), {target:{value:'first'}});
    fireEvent.click(within(dialog).getByText('保存正文'));
    fireEvent.change(within(dialog).getByLabelText('正文'), {target:{value:'first plus more'}});
    // A third-party update observed by refresh must not be silently adopted.
    f.api.scene.mockResolvedValue({...initial,nodes:[{...node,revision:8}]});
    await act(async () => finish({success:true,data:{object_id:'root'}}));
    expect(within(dialog).getByLabelText('正文')).toHaveValue('first plus more');
    fireEvent.click(within(dialog).getByText('保存正文'));
    await waitFor(()=>expect(f.api.command).toHaveBeenCalledTimes(2));
    expect(f.api.command.mock.calls[1][1]).toMatchObject({body:'first plus more',expected_revision:2});
  });
  it('checks an uncertain save inside the dialog without discarding later body edits', async () => {
    f.api.command.mockRejectedValueOnce(new Error('network lost'));
    show(); await ready(); fireEvent.click(screen.getByRole('button', {name:'展开编辑'}));
    const dialog = await screen.findByRole('dialog', {name:'编辑内容'});
    fireEvent.change(within(dialog).getByLabelText('正文'), {target:{value:'sent'}});
    fireEvent.click(within(dialog).getByText('保存正文'));
    await within(dialog).findByRole('alert');
    fireEvent.change(within(dialog).getByLabelText('正文'), {target:{value:'newer'}});
    fireEvent.click(within(dialog).getByText('核对保存结果'));
    await waitFor(()=>expect(within(dialog).queryByText('核对保存结果')).not.toBeInTheDocument());
    expect(f.api.command.mock.calls[1]).toEqual(f.api.command.mock.calls[0]);
    expect(within(dialog).getByLabelText('正文')).toHaveValue('newer');
  });
  it('keeps a changed title after replaying an uncertain creation and retries as rename', async () => {
    f.api.command.mockRejectedValueOnce(new Error('network lost'));
    show(); await ready();
    fireEvent.doubleClick(screen.getByLabelText('知识画布'),{clientX:500,clientY:500});
    const input=screen.getByLabelText('新主题标题');
    fireEvent.change(input,{target:{value:'sent'}});fireEvent.keyDown(input,{key:'Enter'});
    await screen.findAllByRole('alert');
    fireEvent.change(input,{target:{value:'changed'}});
    f.api.scene.mockResolvedValue({...initial,nodes:[node,{...node,id:'new',node_id:'new',title:'sent',x:500,y:500}]});
    fireEvent.keyDown(input,{key:'Enter'});
    const renamed = await screen.findByLabelText('节点标题');
    expect(renamed).toHaveValue('changed');
    expect(f.api.command.mock.calls[1]).toEqual(f.api.command.mock.calls[0]);
    fireEvent.keyDown(renamed,{key:'Enter'});
    await waitFor(()=>expect(f.api.command).toHaveBeenCalledTimes(3));
    expect(f.api.command.mock.calls[2]).toEqual(['update_title',{object_id:'new',expected_revision:1,title:'changed'},expect.any(String)]);
  });
  it('keeps title and body typed during creation and saves onto the created node', async () => {
    let finish!: (v: unknown) => void;
    f.api.command.mockImplementationOnce(() => new Promise(resolve=>{finish=resolve;}));
    const view=show();await ready();fireEvent.contextMenu(screen.getByLabelText('知识画布'),{clientX:600,clientY:500});
    fireEvent.click(screen.getByText('创建文档'));
    let dialog=screen.getByRole('dialog',{name:'创建内容'});
    fireEvent.change(within(dialog).getByLabelText('标题'),{target:{value:'sent title'}});
    fireEvent.change(within(dialog).getByLabelText('正文'),{target:{value:'sent body'}});
    fireEvent.click(within(dialog).getByText('保存到画布'));
    fireEvent.change(within(dialog).getByLabelText('标题'),{target:{value:'later title'}});
    fireEvent.change(within(dialog).getByLabelText('正文'),{target:{value:'later body'}});
    f.api.scene.mockResolvedValue({...initial,nodes:[node,{...node,id:'new',node_id:'new',title:'sent title'}]});
    await act(async()=>finish({success:true,data:{object_id:'new'}}));
    expect(within(dialog).getByLabelText('标题')).toHaveValue('later title');
    expect(within(dialog).getByLabelText('正文')).toHaveValue('later body');
    view.unmount(); sessionStorage.clear();
    show(); await ready(); fireEvent.click(await screen.findByRole('button',{name:'恢复草稿'}));
    dialog=await screen.findByRole('dialog',{name:'创建内容'});
    expect(within(dialog).getByLabelText('正文')).toHaveValue('later body');
    fireEvent.click(within(dialog).getByText('保存到画布'));
    await waitFor(()=>expect(f.api.command).toHaveBeenCalledTimes(3));
    expect(f.api.command.mock.calls[1]).toEqual(['update_title',expect.objectContaining({object_id:'new',title:'later title',expected_revision:1}),expect.any(String)]);
    expect(f.api.command.mock.calls[2]).toEqual(['save_document',expect.objectContaining({object_id:'new',body:'later body',expected_revision:2}),expect.any(String)]);
    expect(f.api.command.mock.calls.filter(c=>c[0]==='create_object')).toHaveLength(1);
  });
  it('preserves inline text typed while quick creation is pending', async () => {
    let finish!: (v: unknown) => void;
    f.api.command.mockImplementationOnce(() => new Promise(resolve=>{finish=resolve;}));
    show();await ready();fireEvent.doubleClick(screen.getByLabelText('知识画布'),{clientX:500,clientY:500});
    const input=screen.getByLabelText('新主题标题');
    fireEvent.change(input,{target:{value:'sent'}});fireEvent.keyDown(input,{key:'Enter'});
    expect(input).not.toHaveAttribute('readonly');
    fireEvent.change(input,{target:{value:'later'}});
    f.api.scene.mockResolvedValue({...initial,nodes:[node,{...node,id:'new',node_id:'new',title:'sent'}]});
    await act(async()=>finish({success:true,data:{object_id:'new'}}));
    expect(screen.getByLabelText('节点标题')).toHaveValue('later');
    expect(screen.getByLabelText('节点标题')).toBe(input);
  });
  it('checks uncertain modal creation with the original receipt and keeps changed content', async () => {
    f.api.command.mockRejectedValueOnce(new Error('network lost'));
    show();await ready();fireEvent.contextMenu(screen.getByLabelText('知识画布'),{clientX:600,clientY:500});
    fireEvent.click(screen.getByText('创建文档'));
    const dialog=screen.getByRole('dialog',{name:'创建内容'});
    fireEvent.change(within(dialog).getByLabelText('标题'),{target:{value:'sent'}});
    fireEvent.click(within(dialog).getByText('保存到画布'));
    await within(dialog).findByRole('alert');
    fireEvent.change(within(dialog).getByLabelText('标题'),{target:{value:'later'}});
    fireEvent.change(within(dialog).getByLabelText('正文'),{target:{value:'more'}});
    f.api.scene.mockResolvedValue({...initial,nodes:[node,{...node,id:'new',node_id:'new',title:'sent'}]});
    fireEvent.click(within(dialog).getByText('核对保存结果'));
    await waitFor(()=>expect(within(dialog).queryByText('核对保存结果')).not.toBeInTheDocument());
    expect(f.api.command.mock.calls[1]).toEqual(f.api.command.mock.calls[0]);
    expect(within(dialog).getByLabelText('标题')).toHaveValue('later');
    expect(within(dialog).getByLabelText('正文')).toHaveValue('more');
  });
  it('selects readonly nodes without attempting a write', async () => {
    vi.stubGlobal('PointerEvent', class extends MouseEvent { pointerId: number; constructor(type: string, init: PointerEventInit = {}) { super(type, init); this.pointerId = init.pointerId ?? 1; } });
    f.api.scene.mockResolvedValue({...initial,containers:[{...initial.containers[0],role:'viewer'}]});
    show();await ready();const card=screen.getByRole('article',{name:'项目总纲'});
    fireEvent.pointerDown(card,{button:0,pointerId:1});
    expect(screen.getByText('已选 1 个节点（含后代 1 个）')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    fireEvent.pointerMove(screen.getByLabelText('知识画布'),{pointerId:1,clientX:900,clientY:900});
    fireEvent.pointerUp(screen.getByLabelText('知识画布'),{pointerId:1});
    expect(f.api.command).not.toHaveBeenCalled();
  });
});

describe('association lifecycle', () => {
  const b={...node,id:'b',node_id:'nb',parent_id:'root',title:'第二节点',x:500,y:40};
  const c={...node,id:'c',node_id:'nc',parent_id:'root',title:'第三节点',x:500,y:330};
  const edge={id:'edge',container_id:'p',source_object_id:'root',target_object_id:'b',relation_type:'related'};
  beforeEach(()=>{
    class TestPointerEvent extends MouseEvent {pointerId:number;constructor(type:string,options:PointerEventInit={}){super(type,options);this.pointerId=options.pointerId??1;}}
    vi.stubGlobal('PointerEvent',TestPointerEvent);
    f.api.scene.mockResolvedValue({...initial,nodes:[node,b,c],relations:[edge]});
  });
  const line=()=>screen.getByRole('button',{name:'关联线：项目总纲 → 第二节点'});
  function geometry(){
    const box=(x:number,y:number,w:number,h:number)=>({x,y,left:x,top:y,width:w,height:h,right:x+w,bottom:y+h,toJSON:()=>({})}) as DOMRect;
    const canvas=screen.getByLabelText('知识画布');canvas.getBoundingClientRect=()=>box(0,0,1000,800);
    screen.getByRole('article',{name:'项目总纲'}).getBoundingClientRect=()=>box(80,135,272,170);
    screen.getByRole('article',{name:'第二节点'}).getBoundingClientRect=()=>box(560,135,272,170);
    screen.getByRole('article',{name:'第三节点'}).getBoundingClientRect=()=>box(560,425,272,170);
    Object.defineProperty(document,'elementFromPoint',{configurable:true,value:()=>canvas});
    return canvas;
  }
  it('selects the line alone, shows endpoint handles, and clears selection on Escape or node click',async()=>{
    show();await ready();fireEvent.click(line());
    expect(line()).toHaveAttribute('aria-pressed','true');
    expect(screen.getByLabelText('拖动关联线起点重新连接')).toBeInTheDocument();
    expect(screen.getByLabelText('拖动关联线终点重新连接')).toBeInTheDocument();
    expect(document.querySelectorAll('.kc-node.selected')).toHaveLength(0);
    fireEvent.keyDown(screen.getByLabelText('知识画布'),{key:'Escape'});
    expect(line()).toHaveAttribute('aria-pressed','false');
    fireEvent.click(line());fireEvent.pointerDown(screen.getByRole('article',{name:'第二节点'}),{button:0,pointerId:1});
    expect(line()).toHaveAttribute('aria-pressed','false');
  });
  it.each(['Backspace','Delete'])('deletes only the line using %s',async key=>{
    show();await ready();fireEvent.click(line());fireEvent.keyDown(screen.getByLabelText('知识画布'),{key});
    await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('delete_relation',{container_id:'p',expected_container_revision:12,relation_id:'edge'},expect.any(String)));
    expect(f.api.command).toHaveBeenCalledTimes(1);
  });
  it.each(['source','target'] as const)('reconnects the %s atomically and displays the drop target',async endpoint=>{
    show();await ready();const canvas=geometry();fireEvent.click(line());
    fireEvent.pointerDown(screen.getByLabelText(`拖动关联线${endpoint==='source'?'起点':'终点'}重新连接`),{button:0,pointerId:1,clientX:400,clientY:220});
    fireEvent.pointerMove(canvas,{pointerId:1,clientX:600,clientY:470});
    expect(screen.getByLabelText('关联线重连预览')).toBeInTheDocument();
    expect(screen.getByRole('article',{name:'第三节点'})).toHaveClass('kc-drop-target');
    expect(screen.getByText('松手重新连接 · Esc 保留原线')).toBeInTheDocument();
    fireEvent.pointerUp(canvas,{pointerId:1,clientX:600,clientY:470});
    await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('reconnect_relation',{container_id:'p',expected_container_revision:12,relation_id:'edge',source_object_id:endpoint==='source'?'c':'root',target_object_id:endpoint==='target'?'c':'b'},expect.any(String)));
    expect(f.api.command).toHaveBeenCalledTimes(1);
  });
  it.each(['blank','self','escape','cancel','unchanged'])('keeps the original relation after %s',async kind=>{
    show();await ready();const canvas=geometry();fireEvent.click(line());
    fireEvent.pointerDown(screen.getByLabelText('拖动关联线终点重新连接'),{button:0,pointerId:1,clientX:550,clientY:220});
    const cursor=kind==='self'?{clientX:200,clientY:200}:kind==='unchanged'?{clientX:600,clientY:220}:kind==='blank'?{clientX:400,clientY:600}:{clientX:600,clientY:470};
    fireEvent.pointerMove(canvas,{pointerId:1,...cursor});
    if(kind==='escape')fireEvent.keyDown(canvas,{key:'Escape'});
    if(kind==='cancel')await act(async()=>{fireEvent.pointerCancel(canvas,{pointerId:1});});
    fireEvent.pointerUp(canvas,{pointerId:1,...cursor});
    expect(f.api.command).not.toHaveBeenCalled();expect(screen.queryByLabelText('新主题标题')).not.toBeInTheDocument();
    expect(line()).toBeInTheDocument();
  });
  it('allows readonly selection but no delete or reconnect',async()=>{
    f.api.scene.mockResolvedValue({...initial,containers:[{...initial.containers[0],role:'viewer'}],nodes:[node,b,c],relations:[edge]});
    show();await ready();fireEvent.click(line());
    expect(line()).toHaveAttribute('aria-pressed','true');expect(screen.getByText(/已选关联线.*只读/)).toBeInTheDocument();
    expect(screen.queryByLabelText('拖动关联线起点重新连接')).not.toBeInTheDocument();
    expect(screen.getByRole('button',{name:'删除关联线'})).toBeDisabled();
    fireEvent.keyDown(screen.getByLabelText('知识画布'),{key:'Delete'});expect(f.api.command).not.toHaveBeenCalled();
  });
  it.each([{key:'z',metaKey:true,shiftKey:false},{key:'z',ctrlKey:true,shiftKey:false}])('undo restores, selects and reveals the line (%j)',async shortcut=>{
    let state={...initial,nodes:[node,b,c],relations:[edge]};let undone=false;
    f.api.scene.mockImplementation(async()=>structuredClone(state));
    f.api.history.mockImplementation(async()=>({undo:undone?null:{history_id:'h',container_id:'p',operation:'delete_relation',can_apply:true},redo:undone?{history_id:'h',container_id:'p',operation:'delete_relation',can_apply:true}:null}));
    f.api.command.mockImplementation(async op=>{if(op==='delete_relation'||op==='redo_canvas'){state={...state,relations:[]};undone=false;}if(op==='undo_canvas'){state={...state,relations:[edge]};undone=true;}return {success:true,data:{object_id:null}};});
    show();await ready();fireEvent.click(line());fireEvent.click(screen.getByRole('button',{name:'删除关联线'}));
    await waitFor(()=>expect(screen.queryByRole('button',{name:'关联线：项目总纲 → 第二节点'})).not.toBeInTheDocument());
    fireEvent.keyDown(screen.getByLabelText('知识画布'),shortcut);
    await waitFor(()=>expect(line()).toHaveAttribute('aria-pressed','true'));
    expect(screen.getByLabelText('拖动关联线终点重新连接')).toBeInTheDocument();
    fireEvent.keyDown(screen.getByLabelText('知识画布'),shortcut.metaKey?{key:'z',metaKey:true,shiftKey:true}:{key:'y',ctrlKey:true});
    await waitFor(()=>expect(screen.queryByRole('button',{name:'关联线：项目总纲 → 第二节点'})).not.toBeInTheDocument());
  });
});

it('gives parallel and reverse relations separate paths and independent selection',async()=>{
  const target={...node,id:'b',node_id:'nb',title:'目标',x:500};
  const edge={id:'one',container_id:'p',source_object_id:'root',target_object_id:'b',relation_type:'related'};
  f.api.scene.mockResolvedValue({...initial,nodes:[node,target],relations:[edge,{...edge,id:'two',source_object_id:'b',target_object_id:'root'},{...edge,id:'three',relation_type:'reference'}]});
  show();await ready();
  const paths=[...document.querySelectorAll('.kc-relation-hit')].map(p=>p.getAttribute('d'));
  expect(new Set(paths).size).toBe(3);
  for(const name of ['关联线：项目总纲 → 目标（关联）','关联线：目标 → 项目总纲（关联）','关联线：项目总纲 → 目标（引用）']){
    fireEvent.click(screen.getByRole('button',{name}));
    expect(screen.getByRole('button',{name})).toHaveAttribute('aria-pressed','true');
    expect(document.querySelectorAll('.kc-relation.selected')).toHaveLength(1);
  }
});

describe('menu and focus lifecycle',()=>{
  it('keeps canvas navigation, deletion and undo from leaking through an open menu',async()=>{
    show();await ready();fireEvent.contextMenu(screen.getByRole('article',{name:'项目总纲'}),{clientX:700,clientY:700});
    const first=await screen.findByRole('menuitem',{name:'创建主题'});await waitFor(()=>expect(first).toHaveFocus());
    const card=screen.getByRole('article',{name:'项目总纲'});
    for(const event of [{key:'Delete'},{key:'z',ctrlKey:true},{key:'ArrowLeft'},{key:'Enter'}])fireEvent.keyDown(screen.getByLabelText('知识画布'),event);
    expect(card).toHaveClass('selected');expect(f.api.command).not.toHaveBeenCalled();expect(screen.queryByLabelText('新主题标题')).not.toBeInTheDocument();
    fireEvent.keyDown(first,{key:'Escape'});
    await waitFor(()=>expect(screen.getByLabelText('知识画布')).toHaveFocus());
    expect(card).toHaveClass('selected');
  });
  it('moves keyboard focus into a newly created dialog without menu cleanup stealing it',async()=>{
    const user=userEvent.setup();show();await ready();fireEvent.contextMenu(screen.getByLabelText('知识画布'),{clientX:300,clientY:300});
    const first=await screen.findByRole('menuitem',{name:'创建主题'});await waitFor(()=>expect(first).toHaveFocus());
    await user.keyboard('{Enter}');const dialog=await screen.findByRole('dialog',{name:'创建内容'});
    await waitFor(()=>expect(dialog.contains(document.activeElement)).toBe(true));
    expect(screen.getByLabelText('知识画布')).not.toHaveFocus();
    fireEvent.click(within(dialog).getByRole('button',{name:'取消'}));
    await waitFor(()=>expect(screen.getByLabelText('知识画布')).toHaveFocus());
  });
  it.each(['F10','ContextMenu'])('opens from the keyboard using %s, keeping the selected target',async key=>{
    show();await ready();fireEvent.pointerDown(screen.getByRole('article',{name:'项目总纲'}),{button:0,pointerId:1});
    fireEvent.keyDown(screen.getByLabelText('知识画布'),{key,shiftKey:key==='F10'});
    const first=await screen.findByRole('menuitem',{name:'创建主题'});await waitFor(()=>expect(first).toHaveFocus());
    expect(screen.getByRole('menuitem',{name:'修改标题'})).toBeInTheDocument();
    fireEvent.keyDown(first,{key:'Escape'});await waitFor(()=>expect(screen.getByLabelText('知识画布')).toHaveFocus());
  });
  it('returns focus from directory navigation so the next Enter creates at the selected node',async()=>{
    show();await ready();const row=screen.getByRole('button',{name:'◇ 项目总纲'});row.focus();fireEvent.click(row);
    await waitFor(()=>expect(screen.getByLabelText('知识画布')).toHaveFocus());
    fireEvent.keyDown(document.activeElement!,{key:'Enter'});expect(screen.getByLabelText('新主题标题')).toBeInTheDocument();
  });
  it('returns focus and clears old multi-selection after search location',async()=>{
    f.api.search.mockResolvedValue({items:[{...node,excerpt:'搜索命中'}],next_cursor:null});
    show();await ready();fireEvent.change(screen.getByLabelText('搜索当前范围标题与正文'),{target:{value:'项目'}});
    const hit=await screen.findByRole('button',{name:'项目总纲 搜索命中'});hit.focus();fireEvent.click(hit);
    await waitFor(()=>expect(screen.getByLabelText('知识画布')).toHaveFocus());
    expect(screen.getByRole('article',{name:'项目总纲'})).toHaveClass('selected');
    fireEvent.keyDown(document.activeElement!,{key:'F2'});expect(screen.getByLabelText('节点标题')).toBeInTheDocument();
  });
  it.each(['button','Escape'])('returns focus after closing the body editor using %s',async method=>{
    show();await ready();fireEvent.click(screen.getByRole('button',{name:'展开编辑'}));
    const dialog=await screen.findByRole('dialog',{name:'编辑内容'});
    if(method==='button')fireEvent.click(within(dialog).getByRole('button',{name:'关闭'}));else { await waitFor(() => expect(dialog).toHaveAttribute('data-state', 'open')); fireEvent.keyDown(document, {key:'Escape'}); }
    await waitFor(()=>expect(screen.getByLabelText('知识画布')).toHaveFocus());
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
  it('returns focus after a successful body save closes the editor',async()=>{
    show();await ready();fireEvent.click(screen.getByRole('button',{name:'展开编辑'}));
    const dialog=await screen.findByRole('dialog',{name:'编辑内容'});fireEvent.click(within(dialog).getByRole('button',{name:'保存正文'}));
    await waitFor(()=>expect(screen.getByLabelText('知识画布')).toHaveFocus());
  });
  it.each(['button','Escape'])('focuses file controls on opening and returns to canvas after closing via %s',async method=>{
    show();await ready();fireEvent.click(screen.getByRole('button',{name:'文件资料'}));
    const close=await screen.findByRole('button',{name:'关闭文件资料'});await waitFor(()=>expect(close).toHaveFocus());
    if(method==='button')fireEvent.click(close);else fireEvent.keyDown(close,{key:'Escape'});
    await waitFor(()=>expect(screen.getByLabelText('知识画布')).toHaveFocus());
    expect(screen.queryByRole('complementary',{name:'文件资料'})).not.toBeInTheDocument();
  });
  it('returns focus after closing the member dialog',async()=>{
    f.api.members.mockResolvedValue([]);show();await ready();fireEvent.contextMenu(screen.getByRole('article',{name:'项目总纲'}),{clientX:300,clientY:300});
    fireEvent.click(await screen.findByRole('menuitem',{name:'项目成员权限'}));
    const dialog=await screen.findByRole('dialog',{name:'项目成员权限'});fireEvent.click(within(dialog).getByRole('button',{name:'关闭'}));
    await waitFor(()=>expect(screen.getByLabelText('知识画布')).toHaveFocus());
  });
});

it.each(['button','Escape'])('returns source panel focus to the canvas using %s',async method=>{
  const original=window.location.pathname+window.location.search+window.location.hash;
  window.history.replaceState(null,'','/?object=root&revision=1');
  f.api.version.mockResolvedValue({object_id:'root',revision:1,title:'原始依据',body:'历史正文',created_at:'2026-10-23'});
  const view=show();
  try{
    await ready();const close=await screen.findByRole('button',{name:'关闭回答来源'});await waitFor(()=>expect(close).toHaveFocus());
    if(method==='button')fireEvent.click(close);else fireEvent.keyDown(close,{key:'Escape'});
    await waitFor(()=>expect(screen.getByLabelText('知识画布')).toHaveFocus());
    expect(screen.queryByRole('complementary',{name:'回答来源版本'})).not.toBeInTheDocument();
  }finally{view.unmount();window.history.replaceState(null,'',original);}
});

it('does not steal focus from new search input when an earlier locate request completes',async()=>{
  f.api.search.mockResolvedValue({items:[{...node,excerpt:'命中'}],next_cursor:null});
  show();await ready();const input=screen.getByLabelText('搜索当前范围标题与正文');
  fireEvent.change(input,{target:{value:'第一次搜索'}});const hit=await screen.findByRole('button',{name:'项目总纲 命中'});
  let finish!:(value:typeof initial)=>void;f.api.scene.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
  hit.focus();fireEvent.click(hit);input.focus();fireEvent.change(input,{target:{value:'继续输入'}});
  await act(async()=>finish(structuredClone(initial)));
  await waitFor(()=>expect(screen.getByRole('article',{name:'项目总纲'})).toHaveClass('selected'));
  expect(input).toHaveFocus();expect(input).toHaveValue('继续输入');
});
it('keeps native input context menus while editing a title',async()=>{
  show();await ready();fireEvent.doubleClick(screen.getByRole('article',{name:'项目总纲'}));
  const input=screen.getByLabelText('节点标题');
  expect(fireEvent.contextMenu(input,{clientX:200,clientY:200})).toBe(true);
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();expect(input).toHaveFocus();
});

describe('continuous canvas motion', () => {
  let frames: Map<number, FrameRequestCallback>, counter: number;
  function frame(time: number) { act(() => { const pending = [...frames.values()]; frames.clear(); pending.forEach(fn => fn(time)); }); }
  beforeEach(() => {
    frames = new Map(); counter = 0;
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
    vi.stubGlobal('requestAnimationFrame', (fn: FrameRequestCallback) => { frames.set(++counter, fn); return counter; });
    vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id));
    vi.stubGlobal('PointerEvent', class extends MouseEvent { pointerId: number; constructor(type: string, init: PointerEventInit = {}) { super(type, init); this.pointerId = init.pointerId ?? 1; } });
  });
  async function start() {
    show(); await ready(); const canvas = screen.getByLabelText('知识画布');
    canvas.getBoundingClientRect = () => ({ left: 0, top: 0, right: 1000, bottom: 800, width: 1000, height: 800, x: 0, y: 0, toJSON: () => ({}) }); return canvas;
  }
  it('folds with inert branch and line departures, then restores live nodes on immediate expansion', async () => {
    const child = { ...node, id: 'child', node_id: 'lc', parent_id: 'root', title: 'Child', x: 350 };
    const grand = { ...child, id: 'grand', node_id: 'lg', parent_id: 'child', title: 'Grand', x: 680 };
    f.api.scene.mockResolvedValue({ ...initial, nodes: [node, child, grand], relations: [{ id: 'r', container_id: 'p', source_object_id: 'child', target_object_id: 'grand', relation_type: 'related' }] });
    await start(); fireEvent.click(screen.getByLabelText('折叠或展开 项目总纲'));
    expect(screen.queryByRole('article', { name: 'Child' })).not.toBeInTheDocument();
    const ghost = document.querySelector('[data-departure-id="child"]'); expect(ghost).toHaveAttribute('inert'); expect(ghost).toHaveAttribute('aria-hidden', 'true');
    expect(document.querySelectorAll('.kc-departing-edges')).toHaveLength(2);
    fireEvent.click(screen.getByLabelText('折叠或展开 项目总纲'));
    expect(screen.getByRole('article', { name: 'Child' })).toHaveClass('kc-node-enter'); expect(screen.getByRole('article', { name: 'Grand' })).toHaveClass('kc-node-enter');
    expect(document.querySelector('[data-departure-id]')).toBeNull(); expect(f.api.command).not.toHaveBeenCalled();
  });
  it('does not animate pending or rejected deletion, and retires confirmed deleted nodes from commands immediately', async () => {
    const canvas = await start(); fireEvent.pointerDown(screen.getByRole('article', { name: '项目总纲' }), { button: 0, pointerId: 1, clientX: 100, clientY: 180 }); fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 100, clientY: 180 });
    let resolve!: (data: unknown) => void; f.api.command.mockImplementationOnce(() => new Promise(ok => { resolve = ok; }));
    fireEvent.keyDown(canvas, { key: 'Delete' }); expect(document.querySelector('.kc-node-exit')).toBeNull(); expect(screen.getByRole('article', { name: '项目总纲' })).toBeInTheDocument();
    f.api.scene.mockResolvedValue({ ...initial, nodes: [] });
    await act(async () => resolve({ success: true, data: {} }));
    expect(document.querySelector('[data-departure-id="root"]')).toHaveClass('kc-node-exit'); expect(screen.queryByRole('article', { name: '项目总纲' })).not.toBeInTheDocument();
    fireEvent.keyDown(canvas, { key: 'Delete' }); expect(f.api.command).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(document.querySelector('[data-departure-id]')).toBeNull());
  });
  it('does not show successful history or departure feedback after a rejected command', async () => {
    f.api.history.mockResolvedValue({ undo: { history_id: 'h', operation: 'delete_branches', can_apply: true, container_id: 'p' } });
    const canvas = await start(); f.api.command.mockRejectedValue(Object.assign(new Error('权限已变化'), { status: 403 }));
    fireEvent.keyDown(canvas, { key: 'z', ctrlKey: true }); await screen.findByRole('alert');
    expect(screen.queryByText('已撤销：删除分支')).not.toBeInTheDocument(); expect(document.querySelector('.kc-node-exit')).toBeNull(); expect(document.querySelector('.kc-node-enter')).toBeNull();
  });
  it.each(['undo', 'redo'])('animates %s changes, retains selection and reports completion', async direction => {
    f.api.history.mockImplementation(async () => ({ [direction]: { history_id: 'h', operation: 'update_title', can_apply: true, container_id: 'p' } }));
    const canvas = await start();
    f.api.scene.mockResolvedValue({ ...initial, nodes: [{ ...node, title: '历史标题', revision: 2, x: 500 }] });
    fireEvent.keyDown(canvas, direction === 'undo' ? { key: 'z', metaKey: true } : { key: 'y', ctrlKey: true });
    const article = await screen.findByRole('article', { name: '历史标题' }); expect(article).toHaveClass('kc-node-emphasis'); expect(article).toHaveClass('selected');
    expect(article.style.left).toBe('20px'); frame(0); frame(80); expect(parseFloat(article.style.left)).toBeGreaterThan(20); frame(160); expect(article.style.left).toBe('500px');
    expect(screen.getByText('已' + (direction === 'undo' ? '撤销' : '重做') + '：改名')).toBeInTheDocument();
  });
  it('fades in created nodes only after acknowledgment and preserves immediate inline editing', async () => {
    const canvas = await start(); fireEvent.doubleClick(canvas, { clientX: 600, clientY: 400 });
    const input = screen.getByLabelText('新主题标题'); fireEvent.change(input, { target: { value: '新节点' } });
    f.api.scene.mockResolvedValue({ ...initial, nodes: [node, { ...node, id: 'new', node_id: 'ln', title: '新节点', x: 540, y: 305 }] });
    fireEvent.keyDown(input, { key: 'Enter' }); const created = await screen.findByRole('article', { name: '新节点' }); expect(created).toHaveClass('kc-node-enter');
    await waitFor(() => expect(screen.getByLabelText('新主题标题')).toHaveValue('')); expect(screen.getByLabelText('新主题标题')).toHaveFocus(); fireEvent.keyDown(screen.getByLabelText('新主题标题'), { key: 'Escape' }); fireEvent.keyDown(canvas, { key: 'F2' }); expect(screen.getByLabelText('节点标题')).toHaveValue('新节点');
  });
  it('grabs an interrupted landing at its visible position and cancels back without another command', async () => {
    f.api.history.mockResolvedValue({ undo: { history_id: 'h', operation: 'move_branch', can_apply: true, container_id: 'p' } });
    const canvas = await start(); f.api.scene.mockResolvedValue({ ...initial, nodes: [{ ...node, x: 500 }] });
    fireEvent.keyDown(canvas, { key: 'z', ctrlKey: true }); await waitFor(() => expect(f.api.command).toHaveBeenCalledTimes(1));
    await act(async () => {}); frame(0); frame(80);
    const article = screen.getByRole('article', { name: '项目总纲' }), visible = article.style.left;
    expect(parseFloat(visible)).toBeGreaterThan(20); expect(parseFloat(visible)).toBeLessThan(500);
    fireEvent.pointerDown(article, { button: 0, pointerId: 2, clientX: 600, clientY: 300 }); expect(article.style.left).toBe(visible);
    frame(120); expect(article.style.left).toBe(visible);
    fireEvent.pointerMove(canvas, { pointerId: 2, clientX: 620, clientY: 300 }); expect(parseFloat(article.style.left)).toBeCloseTo(parseFloat(visible) + 20);
    fireEvent.keyDown(canvas, { key: 'Escape' }); frame(200); frame(360); expect(article.style.left).toBe('500px'); expect(f.api.command).toHaveBeenCalledTimes(1);
  });
  it('animates button zoom, accumulates repeated clicks and immediately yields to a wheel pan', async () => {
    const canvas = await start(), world = document.querySelector('.kc-world')!;
    const before = world.getAttribute('style'); fireEvent.click(screen.getByLabelText('放大')); fireEvent.click(screen.getByLabelText('放大'));
    expect(world.getAttribute('style')).toBe(before); frame(0); frame(110); expect(world.getAttribute('style')).not.toBe(before);
    frame(220); expect(world.getAttribute('style')).toContain('scale(1.44)');
    fireEvent.click(screen.getByLabelText('缩小')); frame(300); frame(380);
    fireEvent.wheel(canvas, { deltaX: 20, deltaY: 30, clientX: 400, clientY: 300 }); const interrupted = world.getAttribute('style'); frame(800);
    expect(world.getAttribute('style')).toBe(interrupted); expect(f.api.command).not.toHaveBeenCalled();
  });
  it('keeps dragged branches under the pointer, shows a separate slot and smoothly rolls back failed saves', async () => {
    const a = { ...node, id: 'a', node_id: 'la', title: 'A', parent_id: 'root', x: 350, y: 40 };
    const b = { ...a, id: 'b', node_id: 'lb', title: 'B', y: 350 };
    f.api.scene.mockResolvedValue({ ...initial, nodes: [node, a, b] });
    let reject!: (error: Error) => void; f.api.command.mockImplementation(() => new Promise((_, no) => { reject = no; }));
    const canvas = await start(), article = screen.getByRole('article', { name: 'A', exact: true });
    fireEvent.pointerDown(article, { button: 0, pointerId: 1, clientX: 430, clientY: 160 });
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 480, clientY: 510 });
    expect(article.style.left).toBe('400px'); expect(article.style.top).toBe('390px');
    expect(screen.getByLabelText('分支落点预览')).toBeInTheDocument(); expect(screen.getByRole('article', { name: 'B', exact: true }).style.top).toBe('350px');
    fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 480, clientY: 510 });
    expect(article.style.left).toBe('400px'); expect(screen.queryByLabelText('分支落点预览')).not.toBeInTheDocument();
    frame(0); frame(80); expect(article.style.left).not.toBe('400px'); const visible = article.style.left;
    await act(async () => reject(new Error('没有编辑权限'))); expect(article.style.left).toBe(visible); frame(200); frame(360);
    expect(article.style.left).toBe('350px'); expect(article.style.top).toBe('40px');
    expect(f.api.command).toHaveBeenCalledWith('move_branch', expect.objectContaining({ object_id: 'a' }), expect.any(String));
  });
  it('smoothly locates a search result and preserves the final focus and target', async () => {
    f.api.search.mockResolvedValue({ items: [{ ...node, excerpt: '搜索命中' }], next_cursor: null });
    const canvas = await start(), world = document.querySelector('.kc-world')!;
    fireEvent.change(screen.getByLabelText('搜索当前范围标题与正文'), { target: { value: '项目' } });
    const hit = await screen.findByRole('button', { name: '项目总纲 搜索命中' }); const before = world.getAttribute('style');
    hit.focus(); await act(async () => { fireEvent.click(hit); }); expect(world.getAttribute('style')).toBe(before);
    frame(0); frame(110); expect(world.getAttribute('style')).not.toBe(before); frame(220);
    expect(world.getAttribute('style')).toContain('translate(360px,295px) scale(1)'); expect(canvas).toHaveFocus();
    expect(screen.getByRole('article', { name: '项目总纲' })).toHaveClass('selected');
  });
  it('places relation selection feedback inside the viewport rather than the page flow', async () => {
    f.api.scene.mockResolvedValue({ ...initial, nodes: [node, { ...node, id: 'other', title: 'Other', x: 400 }], relations: [{ id: 'r', container_id: 'p', source_object_id: 'root', target_object_id: 'other', relation_type: 'related' }] });
    await start(); fireEvent.click(screen.getByRole('button', { name: /关联线：/ }));
    expect(screen.getByLabelText('知识画布').contains(screen.getByRole('group', { name: '选中关联线操作' }))).toBe(true);
  });
});

describe('content sizing and transient feedback P1', () => {
  it('includes downstream layout locks in a long-title rename and keeps the title draft on conflict', async () => {
    const b = { ...node, id: 'b', node_id: 'lb', title: 'B', y: 240, sort_order: 2 };
    f.api.scene.mockResolvedValue({ ...initial, nodes: [{ ...node, sort_order: 1 }, b] });
    show(); await ready(); fireEvent.pointerDown(screen.getByRole('article', { name: '项目总纲' }), { button: 0 });
    fireEvent.keyDown(screen.getByLabelText('知识画布'), { key: 'F2' });
    const input = screen.getByLabelText('节点标题'), title = '长标题验证'.repeat(35);
    f.api.command.mockRejectedValueOnce(Object.assign(new Error('版本冲突'), { status: 409 }));
    fireEvent.change(input, { target: { value: title } }); fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(f.api.command).toHaveBeenCalledWith('update_title', expect.objectContaining({ title, expected_container_revision: 12, nodes: [expect.objectContaining({ node_id: 'lb', expected_revision: 1, position: { x: 20, y: 484 } })] }), expect.any(String)));
    expect(input).toHaveValue(title); expect(screen.getByRole('article', { name: 'B' })).toHaveStyle({ top: '240px' });
  });
  it('expires copy feedback without clearing the copied snapshot or paste action', async () => {
    show(); await ready(); fireEvent.pointerDown(screen.getByRole('article', { name: '项目总纲' }), { button: 0 });
    fireEvent.click(screen.getByRole('button', { name: '复制分支', exact: true })); await snapshotSettled();
    expect(screen.getByText(/已复制 1 个节点的内容快照/)).toHaveClass('kc-operation-note');
    await waitFor(() => expect(screen.queryByText(/已复制 1 个节点的内容快照/)).toBeNull(), { timeout: 3500 });
    expect(screen.getByRole('button', { name: '粘贴分支', exact: true })).toBeEnabled();
  });
});

describe('Shift combinations and safe browsing while saving', () => {
  const a = { ...node, id: 'a', node_id: 'la', title: 'A', parent_id: 'root', x: 350, sort_order: 1 };
  const b = { ...a, id: 'b', node_id: 'lb', title: 'B', y: 300, sort_order: 2 };
  const child = { ...a, id: 'child', node_id: 'lc', title: 'Child', parent_id: 'b', x: 680, y: 300 };
  beforeEach(() => {
    vi.stubGlobal('PointerEvent', class extends MouseEvent { pointerId: number; constructor(type: string, init: PointerEventInit = {}) { super(type, init); this.pointerId = init.pointerId ?? 1; } });
    f.api.scene.mockResolvedValue({ ...initial, nodes: [node, a, b, child] });
  });
  function pick(name: string) { const canvas = screen.getByLabelText('知识画布'); fireEvent.pointerDown(screen.getByRole('article', { name, exact: true }), { button: 0, pointerId: 1, clientX: 430, clientY: 160 }); fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 430, clientY: 160 }); return canvas; }
  it('Shift+Enter inserts before the current topic in one command', async () => {
    show(); await ready(); const canvas = pick('B'); fireEvent.keyDown(canvas, { key: 'Enter', shiftKey: true });
    const input = screen.getByLabelText('新主题标题'); fireEvent.change(input, { target: { value: 'B之前' } }); fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(f.api.command).toHaveBeenCalledWith('create_object', expect.objectContaining({ before_object_id: 'b', parent_id: 'root', title: 'B之前', expected_container_revision: 12, nodes: expect.any(Array) }), expect.any(String)));
    expect(f.api.command.mock.calls[0][1].after_object_id).toBeUndefined();
  });
  it.each(['Tab', 'F2', ' ', 'Delete'])('does not reinterpret Shift+%s as a plain destructive or creation shortcut', async key => {
    show(); await ready(); const canvas = pick('A'); fireEvent.keyDown(canvas, { key, code: key === ' ' ? 'Space' : key, shiftKey: true });
    expect(screen.queryByLabelText('新主题标题')).toBeNull(); expect(screen.queryByLabelText('节点标题')).toBeNull(); expect(f.api.command).not.toHaveBeenCalled();
  });
  it('preserves native reverse-tab focus and IME/modified input boundaries', async () => {
    show(); await ready(); const canvas = pick('A'); canvas.focus(); await userEvent.setup().tab({ shift: true }); expect(canvas).not.toHaveFocus();
    fireEvent.keyDown(canvas, { key: 'Enter', shiftKey: true, isComposing: true }); expect(screen.queryByLabelText('新主题标题')).toBeNull();
    fireEvent.keyDown(canvas, { key: 'F2' }); const input = screen.getByLabelText('节点标题'); fireEvent.keyDown(input, { key: 'Enter', shiftKey: true }); fireEvent.keyDown(input, { key: 'Tab', shiftKey: true });
    expect(f.api.command).not.toHaveBeenCalled(); expect(input).toBeInTheDocument();
  });
  it('allows selection, keyboard folding and pan during save but forbids a second write and preserves the new selection', async () => {
    show(); await ready(); const canvas = pick('A');
    canvas.getBoundingClientRect = () => ({ left: 0, top: 0, right: 1000, bottom: 800, width: 1000, height: 800, x: 0, y: 0, toJSON: () => ({}) });
    let finish!: (value: unknown) => void; f.api.command.mockImplementationOnce(() => new Promise(ok => { finish = ok; })); fireEvent.keyDown(canvas, { key: 'Delete' });
    pick('B'); expect(screen.getByRole('article', { name: 'B', exact: true })).toHaveClass('selected');
    fireEvent.keyDown(canvas, { key: 'ArrowLeft' }); expect(screen.getByLabelText('折叠或展开 B')).toHaveAttribute('aria-expanded', 'false');
    for (const key of ['Delete', 'Enter', 'Tab', 'F2']) fireEvent.keyDown(canvas, { key });
    expect(f.api.command).toHaveBeenCalledTimes(1); expect(screen.queryByLabelText('新主题标题')).toBeNull();
    fireEvent.pointerDown(canvas, { button: 1, pointerId: 2, clientX: 400, clientY: 300 }); fireEvent.pointerMove(canvas, { pointerId: 2, clientX: 440, clientY: 330 }); fireEvent.pointerUp(canvas, { pointerId: 2, clientX: 440, clientY: 330 });
    const world = document.querySelector('.kc-world')!, view = world.getAttribute('style');
    f.api.scene.mockResolvedValue({ ...initial, nodes: [node, b, child] }); await act(async () => finish({ success: true, data: {} }));
    expect(screen.getByRole('article', { name: 'B', exact: true })).toHaveClass('selected'); expect(world.getAttribute('style')).toBe(view);
  });
  it('locates search hits during a pending write without starting a competing scene refresh', async () => {
    f.api.search.mockResolvedValue({ items: [{ ...b, excerpt: '命中' }], next_cursor: null }); show(); await ready(); const canvas = pick('A');
    let finish!: (value: unknown) => void; f.api.command.mockImplementationOnce(() => new Promise(ok => { finish = ok; })); fireEvent.keyDown(canvas, { key: 'Delete' });
    const reads = f.api.scene.mock.calls.length; fireEvent.change(screen.getByLabelText('搜索当前范围标题与正文'), { target: { value: 'B' } });
    fireEvent.click(await screen.findByRole('button', { name: 'B 命中' })); expect(screen.getByRole('article', { name: 'B', exact: true })).toHaveClass('selected'); expect(f.api.scene).toHaveBeenCalledTimes(reads);
    f.api.scene.mockResolvedValue({ ...initial, nodes: [node, b, child] }); await act(async () => finish({ success: true, data: {} })); expect(screen.getByRole('article', { name: 'B', exact: true })).toHaveClass('selected');
  });
  it('keeps the browsed space after a delayed write completes', async () => {
    const personal = { ...node, id: 'personal-node', node_id: 'lp', container_id: 'personal', title: '个人节点' };
    f.api.scene.mockResolvedValue({ ...initial, nodes: [node, a, b, personal] }); show(); await ready(); const canvas = pick('A');
    let finish!: (value: unknown) => void; f.api.command.mockImplementationOnce(() => new Promise(ok => { finish = ok; })); fireEvent.keyDown(canvas, { key: 'Delete' });
    fireEvent.click(screen.getByRole('tab', { name: '个人知识库', exact: true })); expect(screen.getByRole('article', { name: '个人节点' })).toBeInTheDocument();
    f.api.scene.mockResolvedValue({ ...initial, nodes: [node, b, personal] }); await act(async () => finish({ success: true, data: {} }));
    expect(screen.getByRole('tab', { name: '个人知识库', exact: true })).toHaveAttribute('aria-selected', 'true'); expect(screen.queryByRole('article', { name: 'B', exact: true })).toBeNull();
  });
  it('permits browsing an unknown result without discarding its command identity or enabling writes', async () => {
    show(); await ready(); const canvas = pick('A'); f.api.command.mockRejectedValueOnce(new Error('network unavailable')); fireEvent.keyDown(canvas, { key: 'Delete' }); await screen.findByRole('alert');
    const id = f.api.command.mock.calls[0][2]; pick('B'); fireEvent.keyDown(canvas, { key: 'ArrowRight' }); expect(screen.getByRole('article', { name: 'Child' })).toHaveClass('selected');
    fireEvent.keyDown(canvas, { key: 'Delete' }); expect(f.api.command).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: '刷新', exact: true })); await waitFor(() => expect(f.api.command).toHaveBeenCalledTimes(2)); expect(f.api.command.mock.calls[1][2]).toBe(id);
  });
});

// The same visual dot owns click-to-fold and drag-to-create; no extra plus controls.
describe('unified branch dot', () => {
  beforeEach(() => { vi.stubGlobal('PointerEvent', class extends MouseEvent { pointerId: number; constructor(type: string, init: PointerEventInit = {}) { super(type, init); this.pointerId = init.pointerId ?? 1; } }); });
  it('removes plus controls and folds exactly once on a pointer tap', async () => {
    f.api.scene.mockResolvedValue({ ...initial, nodes: [node, { ...node, id: 'child', node_id: 'child-layout', parent_id: 'root', title: '子主题', x: 350 }] });
    show(); await ready();
    const canvas = screen.getByLabelText('知识画布');
    const dot = screen.getByLabelText('折叠或展开 项目总纲');
    expect(dot).toHaveClass('kc-handle');
    expect(document.querySelector('.kc-add-child,.kc-add-sibling')).toBeNull();
    expect(screen.queryByRole('button', { name: '收起分支' })).toBeNull();
    fireEvent.pointerDown(dot, { button: 0, pointerId: 1, clientX: 240, clientY: 90 });
    fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 240, clientY: 90 });
    fireEvent.click(dot, { detail: 1 });
    expect(dot).toHaveAttribute('aria-expanded', 'false');
    expect(f.api.command).not.toHaveBeenCalled();
    fireEvent.click(dot, { detail: 0 });
    expect(dot).toHaveAttribute('aria-expanded', 'true');
  });
});
