import {restoreTitleSelection,titleSelection} from '../../components/knowledge/titleEditing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import KnowledgeCanvas from '../KnowledgeCanvas';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { createContext, useContext, useState } from 'react';
import userEvent from '@testing-library/user-event';
function changeField(el:HTMLElement,event:{target:{value:string|number}}){if(el.getAttribute('contenteditable')==='true'){el.textContent=String(event.target.value);restoreTitleSelection(el,{start:String(event.target.value).length,end:String(event.target.value).length});fireEvent.input(el,{inputType:'insertText'});}else fireEvent.change(el,event);}
function fieldValue(el:HTMLElement){return el.getAttribute('contenteditable')==='true'?el.textContent:(el as HTMLInputElement).value;}
const TestContent = createContext<React.ReactNode>(null);
function Content() { return useContext(TestContent); }
function MemoryRouter({ children }: { children: React.ReactNode }) { const [router] = useState(() => createMemoryRouter([{ path: '*', element: <Content /> }])); return <TestContent.Provider value={children}><RouterProvider router={router} /></TestContent.Provider>; }

const f = vi.hoisted(() => ({ api: { captureSnapshot: vi.fn(), history: vi.fn(), available: vi.fn(), initialize: vi.fn(), scene: vi.fn(), object: vi.fn(), command: vi.fn(), members: vi.fn(), versions: vi.fn(), version: vi.fn(), search: vi.fn() }, files: { list: vi.fn(), upload: vi.fn(), content: vi.fn(),remove:vi.fn() }, auth: { user: { id: 'u' }, currentOrgId: 'org' as string | null } }));
vi.mock('../../services/knowledge', () => ({ knowledgeApi: f.api, knowledgeFilesApi: f.files }));
vi.mock('../../stores/useAuthStore', () => ({ useAuthStore: (selector: (s: typeof f.auth) => unknown) => selector(f.auth) }));
const node = { id: 'root', node_id: 'placement', container_id: 'p', parent_id: null, kind: 'topic', title: '项目总纲', revision: 1, layout_revision: 1, x: 20, y: 40 };
const initial = { containers: [{ id: 'p', kind: 'project', title: '客户项目', role: 'manager', revision: 12 }, { id: 'personal', kind: 'personal', title: '个人知识库', role: 'manager' }], nodes: [node], relations: [] };
function clickNodeAction(name:string){if(!screen.queryByRole('button',{name,exact:true})){const article=screen.queryByRole('article',{name:node.title,exact:true})??screen.getAllByRole('article')[0];fireEvent.pointerDown(article,{button:0,pointerId:1});fireEvent.pointerUp(screen.getByLabelText('知识画布'),{pointerId:1});}const button=screen.getByRole('button',{name,exact:true});if(name==='文件资料'&&button.getAttribute('aria-haspopup')==='menu'){fireEvent.keyDown(button,{key:'ArrowDown'});fireEvent.click(screen.getByRole('menuitem',{name:'查看全部附件'}));}else fireEvent.click(button);}
function openLabels(){const card=document.querySelector('.kc-node.selected')??screen.getByRole('article',{name:node.title,exact:true});const trigger=within(card as HTMLElement).getByRole('button',{name:'文件资料',exact:true});fireEvent.keyDown(trigger,{key:'ArrowDown'});fireEvent.click(screen.getByRole('menuitem',{name:'标签',exact:true}));}
function openNote(){fireEvent.contextMenu(screen.getByRole('article',{name:node.title,exact:true}),{clientX:200,clientY:200});fireEvent.click(screen.getByRole('menuitem',{name:'备注',exact:true}));}
function show() { return render(<MemoryRouter><KnowledgeCanvas /></MemoryRouter>); }
async function snapshotSettled() { await waitFor(() => expect(screen.queryByText('正在固定复制内容快照…')).not.toBeInTheDocument()); }
async function ready() { await screen.findByText('已连接'); }
beforeEach(() => {
  vi.resetAllMocks();
  URL.createObjectURL=vi.fn(()=> 'blob:knowledge-preview');URL.revokeObjectURL=vi.fn();
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
  it('opens version history from the node context menu',async()=>{
    f.api.versions.mockResolvedValue({items:[{revision:1,title:'原始标题',created_at:'2026-10-22T00:00:00Z'}],next_cursor:null});
    f.api.version.mockResolvedValue({revision:1,title:'原始标题',body:'历史正文'});
    show();await ready();fireEvent.contextMenu(screen.getByRole('article',{name:'项目总纲'}),{clientX:200,clientY:200});
    fireEvent.keyDown(screen.getByRole('menuitem',{name:'更多',exact:true}),{key:'ArrowRight'});fireEvent.click(await screen.findByRole('menuitem',{name:'历史版本'}));const history=await screen.findByRole('dialog',{name:'历史版本'});
    fireEvent.click(within(history).getByText('查看历史版本'));fireEvent.click(await within(history).findByRole('button',{name:/版本 1/}));
    expect(fieldValue(await within(history).findByLabelText('历史版本正文'))).toBe('历史正文');expect(f.api.command).not.toHaveBeenCalled();
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
    changeField(input, { target: { value: '新分支' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(f.api.command).toHaveBeenCalledWith('create_object', expect.objectContaining({ container_id: 'p', title: '新分支', kind: 'topic' }), expect.any(String)));
  });
  it('preserves an editing draft when another writer caused a version conflict', async () => {
    show(); await ready(); clickNodeAction('目录资料');
    const dialog = await screen.findByRole('dialog', { name: '编辑内容' });
    changeField(within(dialog).getByLabelText('正文'), { target: { value: '我尚未保存的修改' } });
    f.api.command.mockRejectedValue(new Error('内容已更新，请刷新'));
    fireEvent.blur(within(dialog).getByLabelText('正文'));
    await within(dialog).findByRole('alert');
    expect(fieldValue(within(dialog).getByLabelText('正文'))).toBe('我尚未保存的修改');
    expect(f.api.command).toHaveBeenCalledWith('save_document', { object_id: 'root', expected_revision: 1, body: '我尚未保存的修改' }, expect.any(String));
  });
  it('reuses a command identity after an uncertain response instead of creating twice', async () => {
    show(); await ready(); clickNodeAction('目录资料');
    const dialog = await screen.findByRole('dialog', { name: '编辑内容' });
    f.api.command.mockRejectedValueOnce(new Error('network unavailable'));
    changeField(within(dialog).getByLabelText('正文'),{target:{value:'自动保存正文'}});
    fireEvent.blur(within(dialog).getByLabelText('正文'));
    await within(dialog).findByRole('alert');
    const firstId = f.api.command.mock.calls[0][2];
    fireEvent.click(within(dialog).getByRole('button',{name:'核对保存结果'}));
    await waitFor(() => expect(f.api.command).toHaveBeenCalledTimes(2));
    expect(f.api.command.mock.calls[1][2]).toBe(firstId);
  });
  it('offers no save action for a viewer', async () => {
    f.api.scene.mockResolvedValue({ ...initial, containers: [{ ...initial.containers[0], role: 'viewer' }] });
    f.api.object.mockResolvedValue({ ...node, body: '只读内容', can_write: false });
    show(); await ready(); clickNodeAction('展开查看');
    const dialog = await screen.findByRole('dialog', { name: '编辑内容' });
    expect(within(dialog).getByLabelText('正文')).toHaveAttribute('readonly');
    expect(within(dialog).queryByRole('button', { name: '保存正文' })).not.toBeInTheDocument();
    expect(document.querySelector('.kc-handle')).not.toBeInTheDocument();
  });
  it('Enter on a root inherits its project even while personal navigation is active', async () => {
    show(); await ready(); const canvas = screen.getByLabelText('知识画布');
    fireEvent.contextMenu(screen.getByRole('article', { name: '项目总纲' }), { clientX: 200, clientY: 200 });
    fireEvent.keyDown(screen.getByRole('menu',{name:'画布右键菜单'}),{key:'Escape'});
    // A read/open action must not be necessary to create a sibling.
    fireEvent.pointerDown(screen.getByRole('article', { name: '项目总纲' }), { button: 0, pointerId: 1 });
    fireEvent.keyDown(canvas, { key: 'Enter' });
    changeField(screen.getByLabelText('新主题标题'), { target: { value: '同级主题' } });
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
    changeField(input, { target: { value: '第一条' } }); fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(screen.getByRole('article', { name: '第一条' })).toBeInTheDocument());
    input = screen.getByLabelText('新主题标题');
    expect(fieldValue(input)).toBe(''); expect(input).toHaveFocus();
    changeField(input, { target: { value: '第二条' } }); fireEvent.keyDown(input, { key: 'Tab' });
    await screen.findByRole('article', { name: '第二条' });
    input = screen.getByLabelText('新主题标题'); changeField(input, { target: { value: '下一级' } }); fireEvent.keyDown(input, { key: 'Enter' });
    await screen.findByRole('article', { name: '下一级' });
    expect(f.api.command.mock.calls.map(c => c[1].parent_id)).toEqual(['root', 'root', 'created-2']);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
  it('places repeated children outside occupied cards and reveals a folded branch', async () => {
    persistCreatedNodes(); show(); await ready(); selectRoot();
    fireEvent.keyDown(screen.getByLabelText('知识画布'), { key: 'Tab' });
    changeField(screen.getByLabelText('新主题标题'), { target: { value: '第一个' } });
    fireEvent.blur(screen.getByLabelText('新主题标题'));
    await screen.findByRole('article', { name: '第一个' });
    fireEvent.click(screen.getByRole('button', { name: '折叠或展开 项目总纲' })); selectRoot();
    fireEvent.keyDown(screen.getByLabelText('知识画布'), { key: 'Tab' });
    expect(screen.getByRole('article', { name: '第一个' })).toBeInTheDocument();
    changeField(screen.getByLabelText('新主题标题'), { target: { value: '第二个' } }); fireEvent.blur(screen.getByLabelText('新主题标题'));
    await screen.findByRole('article', { name: '第二个' });
    const first = f.api.command.mock.calls[0][1].position, second = f.api.command.mock.calls[1][1].position;
    expect(second.x).toBe(first.x); expect(second.y - first.y).toBeGreaterThanOrEqual(194);
  });
  it('renames inline with double click, preserves body access and returns keyboard focus', async () => {
    show(); await ready(); fireEvent.doubleClick(screen.getByRole('article', { name: '项目总纲' }));
    const input = screen.getByLabelText('节点标题'); expect(fieldValue(input)).toBe('项目总纲');
    changeField(input, { target: { value: '新总纲' } }); fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(f.api.command).toHaveBeenCalledWith('update_title', { object_id: 'root', expected_revision: 1, title: '新总纲' }, expect.any(String)));
    await waitFor(() => expect(screen.getByLabelText('知识画布')).toHaveFocus());
    expect(f.api.object).not.toHaveBeenCalled(); clickNodeAction('目录资料');
    expect(await screen.findByRole('dialog', { name: '编辑内容' })).toBeInTheDocument();
  });
  it('ignores IME confirmation and modified Enter on both platforms', async () => {
    show(); await ready(); selectRoot(); fireEvent.keyDown(screen.getByLabelText('知识画布'), { key: 'Tab' });
    const input = screen.getByLabelText('新主题标题'); changeField(input, { target: { value: '中文主题' } });
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true });
    fireEvent.keyDown(input, { key: 'Enter', keyCode: 229 });
    fireEvent.keyDown(input, { key: 'Enter', metaKey: true }); fireEvent.keyDown(input, { key: 'Enter', ctrlKey: true });
    expect(f.api.command).not.toHaveBeenCalled(); fireEvent.keyDown(input, { key: 'Escape' });
    expect(screen.queryByLabelText('新主题标题')).not.toBeInTheDocument(); expect(f.api.command).not.toHaveBeenCalled();
  });
  it('keeps failed inline input and reuses the command identity on retry', async () => {
    show(); await ready(); selectRoot(); fireEvent.keyDown(screen.getByLabelText('知识画布'), { key: 'Tab' });
    const input = screen.getByLabelText('新主题标题'); changeField(input, { target: { value: '保留内容' } });
    f.api.command.mockRejectedValueOnce(new Error('network unavailable')); fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(input).not.toHaveAttribute('readonly')); expect(fieldValue(input)).toBe('保留内容');
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
    const input = screen.getByLabelText('新主题标题'); changeField(input, { target: { value: '不重复创建' } });
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
  it.each([false,true])('keeps the round-dot drop through title confirmation with continued typing=%s',async continued=>{
    prepareLineGesture();const state={...structuredClone(initial),nodes:[{...node,canvas_data:{structure:'right' as const}}] as import('../../services/knowledge').KnowledgeNode[]};
    f.api.scene.mockImplementation(async()=>structuredClone(state));let release!:()=>void;
    f.api.command.mockImplementation(async(op,payload)=>{
      if(op==='create_object'){
        if(continued)await new Promise<void>(r=>{release=r;});
        state.nodes.push({...node,id:'dragged',node_id:'dragged-layout',title:payload.title,parent_id:payload.parent_id,...payload.position,canvas_data:{manual_layout:payload.manual_layout}});
        return{success:true,data:{object_id:'dragged'}};
      }
      if(op==='update_title'){const saved=state.nodes.find(n=>n.id===payload.object_id)!;saved.title=payload.title;for(const move of payload.nodes??[]){const placed=state.nodes.find(n=>n.node_id===move.node_id)!;Object.assign(placed,move.position);}}
      return{success:true,data:{object_id:null}};
    });
    show();await ready();const canvas=lineGeometry();fireEvent.pointerMove(canvas,{pointerId:1,clientX:600,clientY:480});fireEvent.pointerUp(canvas,{pointerId:1,clientX:600,clientY:480});
    const draft=document.querySelector<HTMLElement>('.kc-quick')!,wanted={x:parseFloat(draft.style.left),y:parseFloat(draft.style.top)};
    const input=screen.getByLabelText('新主题标题');changeField(input,{target:{value:'手动落点'}});fireEvent.keyDown(input,{key:'Enter'});
    if(continued){changeField(input,{target:{value:'手动落点补充'}});await act(async()=>release());const editing=await screen.findByLabelText('节点标题');fireEvent.keyDown(editing,{key:'Enter'});}
    await waitFor(()=>expect(screen.queryByLabelText('节点标题')).toBeNull());const saved=await screen.findByRole('article',{name:continued?'手动落点补充':'手动落点'});expect(saved.style.left).toBe(wanted.x+'px');expect(saved.style.top).toBe(wanted.y+'px');
    expect(f.api.command.mock.calls[0][1].position).toEqual(wanted);expect(f.api.command.mock.calls[0][1].manual_layout).toEqual({source_anchor:{x:1,y:.5},target_anchor:{x:0,y:.5}});
    const update=f.api.command.mock.calls.find(c=>c[0]==='update_title');if(update)expect(update[1].nodes??[]).not.toEqual(expect.arrayContaining([expect.objectContaining({node_id:'dragged-layout'})]));
  });
  it('creates from a dragged line inline, then continues child extensions and returns to the new handle', async () => {
    prepareLineGesture(); persistCreatedNodes(); show(); await ready(); const canvas = lineGeometry();
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 450, clientY: 400 });
    expect(screen.getByLabelText('拉线预览')).toBeInTheDocument(); expect(screen.getByText('松手创建子主题 · Esc 取消')).toBeInTheDocument();
    fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 450, clientY: 400 });
    expect(screen.queryByLabelText('拉线预览')).not.toBeInTheDocument(); expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(document.querySelector('.kc-draft-edge')).toBeInTheDocument();
    changeField(screen.getByLabelText('新主题标题'), { target: { value: '拉线子级' } }); fireEvent.keyDown(screen.getByLabelText('新主题标题'), { key: 'Tab' });
    await screen.findByRole('article', { name: '拉线子级' });
    changeField(screen.getByLabelText('新主题标题'), { target: { value: '继续延伸' } }); fireEvent.keyDown(screen.getByLabelText('新主题标题'), { key: 'Enter' });
    await screen.findByRole('article', { name: '继续延伸' });
    expect(screen.queryByLabelText('新主题标题')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '从 继续延伸 拉线创建' })).toBeInTheDocument();
    expect(f.api.command.mock.calls.map(c => c[1].parent_id)).toEqual(['root', 'created-1']);
  });
  it('preserves an empty topic on blur and keeps its exact drop position', async () => {
    prepareLineGesture(); persistCreatedNodes();
    f.api.scene.mockResolvedValue({ ...initial, nodes: [node, { ...node, id: 'occupied', node_id: 'occupied-layout', title: '已有节点', x: 400, y: 300 }] });
    show(); await ready(); const canvas = lineGeometry();
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 450, clientY: 400 }); fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 450, clientY: 400 });
    const draft = document.querySelector<HTMLElement>('.kc-quick')!;
    const wanted = { x: parseFloat(draft.style.left), y: parseFloat(draft.style.top) };
    fireEvent.blur(screen.getByLabelText('新主题标题'));
    await screen.findByRole('article', { name: '新主题' });
    expect(f.api.command).toHaveBeenCalledWith('create_object', expect.objectContaining({ position: wanted, title: '新主题' }), expect.any(String));
    expect(screen.queryByLabelText('新主题标题')).toBeNull();
    expect(screen.queryByText('主题 · 项目总纲')).toBeNull();
  });
  it('saves an empty topic instead of discarding it when its parent dot is pressed', async () => {
    prepareLineGesture(); persistCreatedNodes(); show(); await ready(); const canvas = lineGeometry();
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 450, clientY: 400 }); fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 450, clientY: 400 });
    fireEvent.pointerDown(screen.getByLabelText('从 项目总纲 拉线创建'), { button: 0, pointerId: 2, clientX: 320, clientY: 190 });
    await screen.findByRole('article', { name: '新主题' });
    expect(f.api.command).toHaveBeenCalledTimes(1);
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
    expect(screen.getByText(/松手移动整条分支.*Esc 取消/)).toBeInTheDocument();
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
    fireEvent(title, nativeTitleUndo); expect(nativeTitleUndo.defaultPrevented).toBe(true); expect(f.api.command).not.toHaveBeenCalled();
    fireEvent.keyDown(title, { key: 'Escape' }); clickNodeAction('目录资料');
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
    try { show(); await ready(); expect(fieldValue(await screen.findByLabelText('回答来源正文'))).toBe('固定版本仍保留'); }
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
    const b={...node,id:'b',node_id:'lb',title:'B',y:130};f.api.scene.mockResolvedValue({...structuredClone(initial),nodes:[node,b]});show();await ready();pickRoot();
    fireEvent.keyDown(screen.getByLabelText('知识画布'),{key:'Enter'});changeField(screen.getByLabelText('新主题标题'),{target:{value:'插入节点'}});fireEvent.keyDown(screen.getByLabelText('新主题标题'),{key:'Enter'});
    await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('create_object',expect.objectContaining({after_object_id:'root',expected_container_revision:12,parent_id:null,nodes:expect.arrayContaining([expect.objectContaining({node_id:'lb'})])}),expect.any(String)));
  });
  it('right blank paste ignores old selection and anchors at click in world coordinates',async()=>{
    show();await ready();pickRoot();fireEvent.click(screen.getByRole('button',{name:'复制分支',exact:true}));await snapshotSettled();
    fireEvent.contextMenu(screen.getByLabelText('知识画布'),{clientX:900,clientY:500});fireEvent.click(screen.getByRole('menuitem',{name:'粘贴分支 · Ctrl+V'}));
    await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('copy_branches',expect.objectContaining({parent_id:null,offset:{x:820,y:365}}),expect.any(String)));
  });
  it('right node paste uses the clicked parent and a separate child location',async()=>{
    const target={...node,id:'target',node_id:'lt',title:'目标',x:500};f.api.scene.mockResolvedValue({...structuredClone(initial),nodes:[node,target]});show();await ready();pickRoot();fireEvent.click(screen.getByRole('button',{name:'复制分支',exact:true}));await snapshotSettled();
    fireEvent.contextMenu(screen.getByRole('article',{name:'目标'}),{clientX:700,clientY:300});fireEvent.click(screen.getByRole('menuitem',{name:'粘贴分支 · Ctrl+V'}));
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
    const view = show(); await ready(); clickNodeAction('目录资料');
    changeField(await screen.findByLabelText('正文'), { target: { value: '离页保留的草稿' } });
    await waitFor(() => expect(Object.values(localStorage).join('')).toContain('离页保留的草稿'));
    const event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event); expect(event.defaultPrevented).toBe(true);
    view.unmount(); show(); await ready(); fireEvent.click(await screen.findByRole('button', { name: '恢复草稿' }));
    expect(fieldValue(await screen.findByLabelText('正文'))).toBe('离页保留的草稿');
    expect(f.api.command).not.toHaveBeenCalled();
  });
  it('keeps the original expected revision when restoring a stale body', async () => {
    const view = show(); await ready(); clickNodeAction('目录资料');
    changeField(await screen.findByLabelText('正文'), { target: { value: '我的旧版本草稿' } }); view.unmount();
    f.api.object.mockResolvedValue({ ...node, revision: 5, body: '别人的新正文', can_write: true });
    show(); await ready(); fireEvent.click(await screen.findByRole('button', { name: '恢复草稿' }));
    expect(fieldValue(await screen.findByLabelText('正文'))).toBe('我的旧版本草稿');
    expect(f.api.command).not.toHaveBeenCalled();fireEvent.click(screen.getByRole('button',{name:'重试保存'}));
    await waitFor(() => expect(f.api.command).toHaveBeenCalledWith('save_document', expect.objectContaining({ expected_revision: 1 }), expect.any(String)));
  });
  it('checks current write permission before revealing a recovered body', async () => {
    const view = show(); await ready(); clickNodeAction('目录资料');
    changeField(await screen.findByLabelText('正文'), { target: { value: '不可越权恢复' } }); view.unmount();
    f.api.object.mockResolvedValue({ ...node, body: '', can_write: false });
    show(); await ready(); fireEvent.click(await screen.findByRole('button', { name: '恢复草稿' }));
    expect((await screen.findAllByRole('alert')).some(e => e.textContent?.includes('已无编辑权限'))).toBe(true);
    expect(screen.queryByLabelText('正文')).not.toBeInTheDocument();
  });
  it('isolates recovery from a different organization', async () => {
    const view = show(); await ready(); clickNodeAction('目录资料');
    changeField(await screen.findByLabelText('正文'), { target: { value: '企业一的草稿' } }); view.unmount();
    f.auth.currentOrgId = 'another-org'; show(); await ready();
    expect(screen.queryByRole('button', { name: '恢复草稿' })).not.toBeInTheDocument();
  });
  it('reuses the original command ID after refreshing an uncertain save', async () => {
    const view = show(); await ready(); clickNodeAction('目录资料');
    changeField(await screen.findByLabelText('正文'), { target: { value: '未知结果' } });
    f.api.command.mockRejectedValueOnce(new Error('network')); fireEvent.blur(screen.getByLabelText('正文'));
    await screen.findAllByRole('alert'); const commandId = f.api.command.mock.calls[0][2]; view.unmount();
    show(); await ready(); fireEvent.click(await screen.findByRole('button', { name: '恢复草稿' }));
    await screen.findByLabelText('正文'); fireEvent.click(screen.getByRole('button',{name:'核对保存结果'}));
    await waitFor(() => expect(f.api.command).toHaveBeenLastCalledWith('save_document', expect.objectContaining({ body: '未知结果' }), commandId));
  });
  it('rejects the old cut token after cancel and remount', async () => {
    const view = show(); await ready(); const root = screen.getByRole('article', { name: '项目总纲' });
    fireEvent.contextMenu(root, { clientX: 100, clientY: 100 }); fireEvent.keyDown(screen.getByRole('menu',{name:'画布右键菜单'}),{key:'Escape'});
    let record = ''; const clipboardData = { setData: (mime: string, value: string) => { if (mime === 'application/x-everydayai-branches') record = value; } };
    fireEvent.cut(screen.getByLabelText('知识画布'), { clipboardData }); expect(record).toContain('cut');
    fireEvent.keyDown(screen.getByLabelText('知识画布'), { key: 'Escape' }); view.unmount();
    show(); await ready(); fireEvent.paste(screen.getByLabelText('知识画布'), { clipboardData: { getData: () => record } });
    expect(await screen.findByRole('alert')).toHaveTextContent('页面刷新后需重新剪切'); expect(f.api.command).not.toHaveBeenCalled();
  });
});

it('recovers an uncertain canvas-only operation and checks it with the original command ID', async () => {
  const view = show(); await ready();
  fireEvent.contextMenu(screen.getByRole('article', { name: '项目总纲' }), { clientX: 100, clientY: 100 }); fireEvent.keyDown(screen.getByRole('menu',{name:'画布右键菜单'}),{key:'Escape'});
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
  changeField(screen.getByLabelText('新主题标题'), { target: { value: '还未按回车' } }); view.unmount();
  show(); await ready(); expect(await screen.findByRole('dialog', { name: '恢复未保存草稿' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '恢复草稿' }));
  expect(fieldValue(await screen.findByLabelText('新主题标题'))).toBe('还未按回车'); expect(f.api.command).not.toHaveBeenCalled();
});

it('preserves drafts when switching organization away and back without unmounting', async () => {
  const view = show(); await ready(); clickNodeAction('目录资料');
  changeField(await screen.findByLabelText('正文'), { target: { value: '返回原企业后恢复' } });
  f.auth.currentOrgId = 'other-org'; view.rerender(<MemoryRouter><KnowledgeCanvas /></MemoryRouter>); await ready();
  expect(screen.queryByRole('button', { name: '恢复草稿' })).not.toBeInTheDocument();
  f.auth.currentOrgId = 'org'; view.rerender(<MemoryRouter><KnowledgeCanvas /></MemoryRouter>); await ready();
  fireEvent.click(await screen.findByRole('button', { name: '恢复草稿' }));
  expect(fieldValue(await screen.findByLabelText('正文'))).toBe('返回原企业后恢复');
});
it('rechecks container permission at restore time instead of using the loaded scene', async () => {
  const view = show(); await ready(); clickNodeAction('目录资料');
  changeField(await screen.findByLabelText('正文'), { target: { value: '不可恢复的草稿' } }); view.unmount(); sessionStorage.clear();
  show(); await ready(); await screen.findByRole('button', { name: '恢复草稿' });
  f.api.scene.mockResolvedValue({ ...structuredClone(initial), containers: [{ ...initial.containers[0], role: 'viewer' }] });
  fireEvent.click(screen.getByRole('button', { name: '恢复草稿' }));
  await waitFor(() => expect(screen.getAllByRole('alert').some(e => e.textContent?.includes('无编辑权限'))).toBe(true));
  expect(screen.queryByLabelText('正文')).not.toBeInTheDocument();
});

describe('复制内容快照和历史限制', () => {
  it('captures on copy, then pastes the snapshot after source deletion with current space revision', async () => {
    let state = structuredClone(initial); f.api.scene.mockImplementation(async () => structuredClone(state));
    show(); await ready(); fireEvent.contextMenu(screen.getByRole('article', { name: '项目总纲' }), { clientX: 200, clientY: 200 }); fireEvent.keyDown(screen.getByRole('menu',{name:'画布右键菜单'}),{key:'Escape'});
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
    show(); await ready(); fireEvent.contextMenu(screen.getByRole('article', { name: '项目总纲' }), { clientX: 100, clientY: 100 }); fireEvent.keyDown(screen.getByRole('menu',{name:'画布右键菜单'}),{key:'Escape'});
    const transfer = { setData: vi.fn() }; fireEvent.copy(screen.getByLabelText('知识画布'), { clipboardData: transfer });
    const record = transfer.setData.mock.calls.find(c => c[0] === 'application/x-everydayai-branches')![1];
    fireEvent.paste(screen.getByLabelText('知识画布'), { clipboardData: { getData: () => record } }); expect(f.api.command).not.toHaveBeenCalled();
    await act(async () => finish()); await waitFor(() => expect(f.api.command).toHaveBeenCalledOnce());
  });
  it('does not fall back to live content when capturing fails', async () => {
    f.api.captureSnapshot.mockRejectedValue(new Error('源内容已变化'));
    show(); await ready(); fireEvent.contextMenu(screen.getByRole('article', { name: '项目总纲' }), { clientX: 100, clientY: 100 }); fireEvent.keyDown(screen.getByRole('menu',{name:'画布右键菜单'}),{key:'Escape'});
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
  const view = show(); await ready(); fireEvent.contextMenu(screen.getByRole('article', { name: '项目总纲' }), { clientX: 100, clientY: 100 }); fireEvent.keyDown(screen.getByRole('menu',{name:'画布右键菜单'}),{key:'Escape'});
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
    show(); await ready(); clickNodeAction('目录资料');
    const dialog = await screen.findByRole('dialog', {name:'编辑内容'});
    changeField(within(dialog).getByLabelText('正文'), {target:{value:'first'}});
    fireEvent.blur(within(dialog).getByLabelText('正文'));
    changeField(within(dialog).getByLabelText('正文'), {target:{value:'first plus more'}});
    // A third-party update observed by refresh must not be silently adopted.
    f.api.scene.mockResolvedValue({...initial,nodes:[{...node,revision:8}]});
    await act(async () => finish({success:true,data:{object_id:'root'}}));
    expect(fieldValue(within(dialog).getByLabelText('正文'))).toBe('first plus more');
    fireEvent.blur(within(dialog).getByLabelText('正文'));
    await waitFor(()=>expect(f.api.command).toHaveBeenCalledTimes(2));
    expect(f.api.command.mock.calls[1][1]).toMatchObject({body:'first plus more',expected_revision:2});
  });
  it('checks an uncertain save inside the dialog without discarding later body edits', async () => {
    f.api.command.mockRejectedValueOnce(new Error('network lost'));
    show(); await ready(); clickNodeAction('目录资料');
    const dialog = await screen.findByRole('dialog', {name:'编辑内容'});
    changeField(within(dialog).getByLabelText('正文'), {target:{value:'sent'}});
    fireEvent.blur(within(dialog).getByLabelText('正文'));
    await within(dialog).findByRole('alert');
    changeField(within(dialog).getByLabelText('正文'), {target:{value:'newer'}});
    fireEvent.click(within(dialog).getByText('核对保存结果'));
    await waitFor(()=>expect(within(dialog).queryByText('核对保存结果')).not.toBeInTheDocument());
    expect(f.api.command.mock.calls[1]).toEqual(f.api.command.mock.calls[0]);
    expect(fieldValue(within(dialog).getByLabelText('正文'))).toBe('newer');
  });
  it('keeps a changed title after replaying an uncertain creation and retries as rename', async () => {
    f.api.command.mockRejectedValueOnce(new Error('network lost'));
    show(); await ready();
    fireEvent.doubleClick(screen.getByLabelText('知识画布'),{clientX:500,clientY:500});
    const input=screen.getByLabelText('新主题标题');
    changeField(input,{target:{value:'sent'}});fireEvent.keyDown(input,{key:'Enter'});
    await screen.findAllByRole('alert');
    changeField(input,{target:{value:'changed'}});
    f.api.scene.mockResolvedValue({...initial,nodes:[node,{...node,id:'new',node_id:'new',title:'sent',x:500,y:500}]});
    fireEvent.keyDown(input,{key:'Enter'});
    const renamed = await screen.findByLabelText('节点标题');
    expect(fieldValue(renamed)).toBe('changed');
    expect(f.api.command.mock.calls[1]).toEqual(f.api.command.mock.calls[0]);
    fireEvent.keyDown(renamed,{key:'Enter'});
    await waitFor(()=>expect(f.api.command).toHaveBeenCalledTimes(3));
    expect(f.api.command.mock.calls[2]).toEqual(['update_title',{object_id:'new',expected_revision:1,title:'changed'},expect.any(String)]);
  });
  it('keeps title and body typed during creation and saves onto the created node', async () => {
    let finish!: (v: unknown) => void;
    f.api.command.mockImplementationOnce(() => new Promise(resolve=>{finish=resolve;}));
    const view=show();await ready();fireEvent.contextMenu(screen.getByLabelText('知识画布'),{clientX:600,clientY:500});
    fireEvent.keyDown(screen.getByRole('menuitem',{name:'创建',exact:true}),{key:'ArrowRight'});fireEvent.click(await screen.findByRole('menuitem',{name:'创建文档'}));
    let dialog=screen.getByRole('dialog',{name:'创建内容'});
    changeField(within(dialog).getByLabelText('标题'),{target:{value:'sent title'}});
    changeField(within(dialog).getByLabelText('正文'),{target:{value:'sent body'}});
    fireEvent.click(within(dialog).getByText('保存到画布'));
    changeField(within(dialog).getByLabelText('标题'),{target:{value:'later title'}});
    changeField(within(dialog).getByLabelText('正文'),{target:{value:'later body'}});
    f.api.scene.mockResolvedValue({...initial,nodes:[node,{...node,id:'new',node_id:'new',title:'sent title'}]});
    await act(async()=>finish({success:true,data:{object_id:'new'}}));
    expect(fieldValue(within(dialog).getByLabelText('标题'))).toBe('later title');
    expect(fieldValue(within(dialog).getByLabelText('正文'))).toBe('later body');
    view.unmount(); sessionStorage.clear();
    show(); await ready(); fireEvent.click(await screen.findByRole('button',{name:'恢复草稿'}));
    dialog=await screen.findByRole('dialog',{name:'创建内容'});
    expect(fieldValue(within(dialog).getByLabelText('正文'))).toBe('later body');
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
    changeField(input,{target:{value:'sent'}});fireEvent.keyDown(input,{key:'Enter'});
    expect(input).not.toHaveAttribute('readonly');
    changeField(input,{target:{value:'later'}});
    f.api.scene.mockResolvedValue({...initial,nodes:[node,{...node,id:'new',node_id:'new',title:'sent'}]});
    await act(async()=>finish({success:true,data:{object_id:'new'}}));
    expect(fieldValue(screen.getByLabelText('节点标题'))).toBe('later');
    expect(screen.getByLabelText('节点标题')).toBe(input);
  });
  it('checks uncertain modal creation with the original receipt and keeps changed content', async () => {
    f.api.command.mockRejectedValueOnce(new Error('network lost'));
    show();await ready();fireEvent.contextMenu(screen.getByLabelText('知识画布'),{clientX:600,clientY:500});
    fireEvent.keyDown(screen.getByRole('menuitem',{name:'创建',exact:true}),{key:'ArrowRight'});fireEvent.click(await screen.findByRole('menuitem',{name:'创建文档'}));
    const dialog=screen.getByRole('dialog',{name:'创建内容'});
    changeField(within(dialog).getByLabelText('标题'),{target:{value:'sent'}});
    fireEvent.click(within(dialog).getByText('保存到画布'));
    await within(dialog).findByRole('alert');
    changeField(within(dialog).getByLabelText('标题'),{target:{value:'later'}});
    changeField(within(dialog).getByLabelText('正文'),{target:{value:'more'}});
    f.api.scene.mockResolvedValue({...initial,nodes:[node,{...node,id:'new',node_id:'new',title:'sent'}]});
    fireEvent.click(within(dialog).getByText('核对保存结果'));
    await waitFor(()=>expect(within(dialog).queryByText('核对保存结果')).not.toBeInTheDocument());
    expect(f.api.command.mock.calls[1]).toEqual(f.api.command.mock.calls[0]);
    expect(fieldValue(within(dialog).getByLabelText('标题'))).toBe('later');
    expect(fieldValue(within(dialog).getByLabelText('正文'))).toBe('more');
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
  it.each(['blank','node','cancel'])('starts a relationship from one selected node to %s',async kind=>{
    show();await ready();const canvas=geometry(),source=screen.getByRole('article',{name:'项目总纲'});fireEvent.pointerDown(source,{button:0,pointerId:1});fireEvent.pointerUp(canvas,{pointerId:1});fireEvent.click(screen.getByRole('button',{name:'关联线',exact:true}));
    expect(screen.getByLabelText('拉线预览')).toBeInTheDocument();const cursor=kind==='node'?{clientX:600,clientY:470}:{clientX:420,clientY:620};fireEvent.pointerMove(canvas,{pointerId:1,...cursor});
    if(kind==='cancel')fireEvent.keyDown(canvas,{key:'Escape'});fireEvent.pointerDown(canvas,{button:0,pointerId:1,...cursor});fireEvent.pointerUp(canvas,{pointerId:1,...cursor});
    if(kind==='cancel')expect(f.api.command).not.toHaveBeenCalled();else await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith(kind==='node'?'connect':'create_relation_topic',expect.objectContaining(kind==='node'?{source_object_id:'root',target_object_id:'c',relation_type:'related'}:{container_id:'p',expected_container_revision:12,source_object_id:'root',position:{x:360,y:525}}),expect.any(String)));
  });
  it('reconnects to blank by creating a floating topic, and freely changes the existing endpoint anchor',async()=>{
    show();await ready();const canvas=geometry();fireEvent.click(line());fireEvent.pointerDown(screen.getByLabelText('拖动关联线终点重新连接'),{button:0,pointerId:1,clientX:560,clientY:220});fireEvent.pointerUp(canvas,{pointerId:1,clientX:420,clientY:620});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('create_relation_topic',expect.objectContaining({source_object_id:'root',relation_id:'edge',endpoint:'target'}),expect.any(String)));
  });
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
    await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('reconnect_relation',expect.objectContaining({container_id:'p',expected_container_revision:12,relation_id:'edge',source_object_id:endpoint==='source'?'c':'root',target_object_id:endpoint==='target'?'c':'b',appearance:expect.objectContaining({[endpoint+'_anchor']:expect.objectContaining({x:0})})}),expect.any(String)));
    expect(f.api.command).toHaveBeenCalledTimes(1);
  });
  it.each(['self','escape','cancel'])('keeps the original relation after %s',async kind=>{
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
    const first=await screen.findByRole('menuitem',{name:'修改排列类型'});await waitFor(()=>expect(first).toHaveFocus());
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
    await user.keyboard('{ArrowDown}{ArrowRight}{Enter}');const dialog=await screen.findByRole('dialog',{name:'创建内容'});
    await waitFor(()=>expect(dialog.contains(document.activeElement)).toBe(true));
    expect(screen.getByLabelText('知识画布')).not.toHaveFocus();
    fireEvent.click(within(dialog).getByRole('button',{name:'取消'}));
    await waitFor(()=>expect(screen.getByLabelText('知识画布')).toHaveFocus());
  });
  it.each(['F10','ContextMenu'])('opens from the keyboard using %s, keeping the selected target',async key=>{
    show();await ready();fireEvent.pointerDown(screen.getByRole('article',{name:'项目总纲'}),{button:0,pointerId:1});
    fireEvent.keyDown(screen.getByLabelText('知识画布'),{key,shiftKey:key==='F10'});
    const first=await screen.findByRole('menuitem',{name:'修改排列类型'});await waitFor(()=>expect(first).toHaveFocus());
    expect(screen.queryByRole('menuitem',{name:'修改标题'})).toBeNull();expect(screen.getByRole('menuitem',{name:'备注'})).toBeInTheDocument();
    fireEvent.keyDown(first,{key:'Escape'});await waitFor(()=>expect(screen.getByLabelText('知识画布')).toHaveFocus());
  });
  it('returns focus from directory navigation so the next Enter creates at the selected node',async()=>{
    show();await ready();const row=screen.getByRole('button',{name:'项目总纲',exact:true});row.focus();fireEvent.click(row);
    await waitFor(()=>expect(screen.getByLabelText('知识画布')).toHaveFocus());
    fireEvent.keyDown(document.activeElement!,{key:'Enter'});expect(screen.getByLabelText('新主题标题')).toBeInTheDocument();
  });
  it('returns focus and clears old multi-selection after search location',async()=>{
    f.api.search.mockResolvedValue({items:[{...node,excerpt:'搜索命中'}],next_cursor:null});
    show();await ready();changeField(screen.getByLabelText('搜索当前范围标题与正文'),{target:{value:'项目'}});
    const hit=await screen.findByRole('button',{name:'项目总纲 搜索命中'});hit.focus();fireEvent.click(hit);
    await waitFor(()=>expect(screen.getByLabelText('知识画布')).toHaveFocus());
    expect(screen.getByRole('article',{name:'项目总纲'})).toHaveClass('selected');
    fireEvent.keyDown(document.activeElement!,{key:'F2'});expect(screen.getByLabelText('节点标题')).toBeInTheDocument();
  });
  it.each(['button','Escape'])('returns focus after closing the body editor using %s',async method=>{
    show();await ready();clickNodeAction('目录资料');
    const dialog=await screen.findByRole('dialog',{name:'编辑内容'});
    if(method==='button')fireEvent.click(within(dialog).getByRole('button',{name:'关闭'}));else { await waitFor(() => expect(dialog).toHaveAttribute('data-state', 'open')); fireEvent.keyDown(document, {key:'Escape'}); }
    await waitFor(()=>expect(screen.getByLabelText('知识画布')).toHaveFocus());
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
  it('keeps the inline editor open after automatic saving',async()=>{
    show();await ready();clickNodeAction('目录资料');
    const dialog=await screen.findByRole('dialog',{name:'编辑内容'});changeField(within(dialog).getByLabelText('正文'),{target:{value:'自动保存'}});fireEvent.blur(within(dialog).getByLabelText('正文'));
    await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('save_document',expect.objectContaining({body:'自动保存'}),expect.any(String)));expect(dialog).toBeInTheDocument();
  });
  it.each(['button','Escape'])('focuses file controls on opening and returns to canvas after closing via %s',async method=>{
    show();await ready();clickNodeAction('文件资料');
    const close=await screen.findByRole('button',{name:'关闭文件资料'});await waitFor(()=>expect(close).toHaveFocus());
    if(method==='button')fireEvent.click(close);else fireEvent.keyDown(close,{key:'Escape'});
    await waitFor(()=>expect(screen.getByLabelText('知识画布')).toHaveFocus());
    expect(screen.queryByRole('complementary',{name:'文件资料'})).not.toBeInTheDocument();
  });
  it('returns focus after closing the member dialog',async()=>{
    f.api.members.mockResolvedValue([]);show();await ready();fireEvent.contextMenu(screen.getByRole('article',{name:'项目总纲'}),{clientX:300,clientY:300});
    fireEvent.keyDown(screen.getByRole('menuitem',{name:'更多',exact:true}),{key:'ArrowRight'});fireEvent.click(await screen.findByRole('menuitem',{name:'项目成员权限'}));
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
  changeField(input,{target:{value:'第一次搜索'}});const hit=await screen.findByRole('button',{name:'项目总纲 命中'});
  let finish!:(value:typeof initial)=>void;f.api.scene.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
  hit.focus();fireEvent.click(hit);input.focus();changeField(input,{target:{value:'继续输入'}});
  await act(async()=>finish(structuredClone(initial)));
  await waitFor(()=>expect(screen.getByRole('article',{name:'项目总纲'})).toHaveClass('selected'));
  expect(input).toHaveFocus();expect(fieldValue(input)).toBe('继续输入');
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
    const input = screen.getByLabelText('新主题标题'); changeField(input, { target: { value: '新节点' } });
    f.api.scene.mockResolvedValue({ ...initial, nodes: [node, { ...node, id: 'new', node_id: 'ln', title: '新节点', x: 540, y: 305 }] });
    fireEvent.keyDown(input, { key: 'Enter' }); const created = await screen.findByRole('article', { name: '新节点' }); expect(created).toHaveClass('kc-node-enter');
    await waitFor(() => expect(fieldValue(screen.getByLabelText('新主题标题'))).toBe('')); expect(screen.getByLabelText('新主题标题')).toHaveFocus(); fireEvent.keyDown(screen.getByLabelText('新主题标题'), { key: 'Escape' }); fireEvent.keyDown(canvas, { key: 'F2' }); expect(fieldValue(screen.getByLabelText('节点标题'))).toBe('新节点');
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
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 460, clientY: 470 });
    expect(article.style.left).toBe('380px'); expect(article.style.top).toBe('350px');
    expect(screen.getByLabelText('分支落点预览')).toBeInTheDocument(); expect(screen.getByRole('article', { name: 'B', exact: true }).style.top).toBe('350px');
    fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 460, clientY: 470 });
    expect(article.style.left).toBe('380px'); expect(screen.queryByLabelText('分支落点预览')).not.toBeInTheDocument();
    frame(0); frame(80); expect(article.style.left).not.toBe('380px'); const visible = article.style.left;
    await act(async () => reject(new Error('没有编辑权限'))); expect(article.style.left).toBe(visible); frame(200); frame(360);
    expect(article.style.left).toBe('350px'); expect(article.style.top).toBe('40px');
    expect(f.api.command).toHaveBeenCalledWith('move_branch', expect.objectContaining({ object_id: 'a' }), expect.any(String));
  });
  it('smoothly locates a search result and preserves the final focus and target', async () => {
    f.api.search.mockResolvedValue({ items: [{ ...node, excerpt: '搜索命中' }], next_cursor: null });
    const canvas = await start(), world = document.querySelector('.kc-world')!;
    changeField(screen.getByLabelText('搜索当前范围标题与正文'), { target: { value: '项目' } });
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
    changeField(input, { target: { value: title } }); fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(f.api.command).toHaveBeenCalledWith('update_title', expect.objectContaining({ title, expected_container_revision: 12, nodes: [expect.objectContaining({ node_id: 'lb', expected_revision: 1, position: { x: 20, y: 336 } })] }), expect.any(String)));
    expect(fieldValue(input)).toBe(title); expect(screen.getByRole('article', { name: 'B' })).toHaveStyle({ top: '240px' });
  });
  it('expires copy feedback without clearing the copied snapshot or paste action', async () => {
    show(); await ready(); fireEvent.pointerDown(screen.getByRole('article', { name: '项目总纲' }), { button: 0 });
    fireEvent.click(screen.getByRole('button', { name: '复制分支', exact: true })); await snapshotSettled();
    expect(await screen.findByText(/已复制 1 个节点的内容快照/)).toHaveClass('kc-operation-note');
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
    const input = screen.getByLabelText('新主题标题'); changeField(input, { target: { value: 'B之前' } }); fireEvent.keyDown(input, { key: 'Enter' });
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
    const reads = f.api.scene.mock.calls.length; changeField(screen.getByLabelText('搜索当前范围标题与正文'), { target: { value: 'B' } });
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

describe('canvas tools and summaries',()=>{
 const a={...node,id:'a',node_id:'la',parent_id:'root',title:'A',x:350,y:40};
 const b={...a,id:'b',node_id:'lb',title:'B',y:300};
 beforeEach(()=>{vi.stubGlobal('PointerEvent',class extends MouseEvent{pointerId:number;constructor(type:string,init:PointerEventInit={}){super(type,init);this.pointerId=init.pointerId??1;}});f.api.scene.mockResolvedValue({...initial,nodes:[node,a,b]});});
 function pick(title:string,shift=false){const canvas=screen.getByLabelText('知识画布');fireEvent.pointerDown(screen.getByRole('article',{name:title,exact:true}),{button:0,pointerId:1,clientX:400,clientY:150,shiftKey:shift});fireEvent.pointerUp(canvas,{pointerId:1,clientX:400,clientY:150});}
 it('creates one summary over selected siblings and undo removes only the summary',async()=>{
  const base={...initial,nodes:[node,a,b]};let current=structuredClone(base);
  f.api.scene.mockImplementation(async()=>structuredClone(current));
  f.api.command.mockImplementation(async(op,p)=>{
   if(op==='create_summaries'){current={...base,nodes:[...base.nodes,{...a,id:'sum',node_id:'lsum',parent_id:null,title:'概要',...p.groups[0].position,canvas_data:{summary:{members:p.groups[0].members,title:'概要'}}}]};f.api.history.mockResolvedValue({undo:{history_id:'h',operation:op,can_apply:true},redo:null});return {success:true,data:{object_id:'sum'}};}
   current=structuredClone(base);f.api.history.mockResolvedValue({undo:null,redo:{history_id:'h',operation:'create_summaries',can_apply:true}});return {success:true,data:{object_id:null}};
  });
  show();await ready();pick('A');pick('B',true);fireEvent.click(screen.getByRole('button',{name:'概要',exact:true}));
  await screen.findByRole('article',{name:'概要',exact:true});
  expect(f.api.command).toHaveBeenCalledWith('create_summaries',expect.objectContaining({groups:[expect.objectContaining({members:['a','b']})]}),expect.any(String));
  expect(current.nodes.filter(n=>n.id==='a'||n.id==='b').map(n=>n.parent_id)).toEqual(['root','root']);expect(screen.getByLabelText('概要连接 概要')).toBeInTheDocument();
  fireEvent.keyDown(screen.getByLabelText('知识画布'),{key:'z',ctrlKey:true});await waitFor(()=>expect(screen.queryByRole('article',{name:'概要',exact:true})).toBeNull());expect(screen.getByRole('article',{name:'A',exact:true})).toBeInTheDocument();
 });
 it('uses an atomic boundary command and native Mac/Windows shortcuts',async()=>{
  show();await ready();pick('A');pick('B',true);
  fireEvent.keyDown(screen.getByLabelText('知识画布'),{key:'b',metaKey:true,shiftKey:true});
  await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_canvas_data',expect.objectContaining({updates:[expect.objectContaining({object_id:'a',canvas_data:{boundaries:[{id:expect.any(String),title:'外框',members:['a','b']}]}})]}),expect.any(String)));
 });
 it('opens notes from F4 and saves its original node even without changing the selection',async()=>{
  show();await ready();pick('A');fireEvent.keyDown(screen.getByLabelText('知识画布'),{key:'F4'});
  changeField(screen.getByLabelText('备注内容'),{target:{value:'补充说明'}});fireEvent.click(screen.getByRole('button',{name:'保存备注'}));
  await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_canvas_data',expect.objectContaining({updates:[expect.objectContaining({object_id:'a',canvas_data:{note:'补充说明'}})]}),expect.any(String)));
 });
});

describe('selected plus buttons',()=>{
 const a={...node,id:'a',node_id:'la',parent_id:'root',title:'A',x:350,y:40};
 beforeEach(()=>{vi.stubGlobal('PointerEvent',class extends MouseEvent{pointerId:number;constructor(type:string,init:PointerEventInit={}){super(type,init);this.pointerId=init.pointerId??1;}});f.api.scene.mockResolvedValue({...initial,nodes:[node,a]});});
 function pick(){const canvas=screen.getByLabelText('知识画布');fireEvent.pointerDown(screen.getByRole('heading',{name:'A',exact:true}),{button:0,pointerId:1,clientX:450,clientY:70});fireEvent.pointerUp(canvas,{pointerId:1,clientX:450,clientY:70});return canvas;}
 it.each(['子级','同级'])('click creates %s using auto layout, and exposes the contextual toolbar',async kind=>{
  show();await ready();const canvas=pick();expect(screen.getByRole('toolbar',{name:'节点操作 A'})).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText('为 A 创建'+kind));changeField(screen.getByLabelText('新主题标题'),{target:{value:'新增'}});fireEvent.keyDown(screen.getByLabelText('新主题标题'),{key:'Enter'});
  await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('create_object',expect.objectContaining({parent_id:kind==='子级'?'a':'root',...(kind==='同级'?{after_object_id:'a'}:{})}),expect.any(String)));
  expect(canvas).toBeInTheDocument();
 });
 it.each(['子级','同级'])('drag creates %s at the drop and preserves the correct parent, including a blank title',async kind=>{
  show();await ready();const canvas=pick();const box=(x:number,y:number,w:number,h:number)=>({left:x,top:y,right:x+w,bottom:y+h,x,y,width:w,height:h,toJSON:()=>({})});canvas.getBoundingClientRect=()=>box(0,0,1200,800);
  screen.getByRole('article',{name:'A',exact:true}).getBoundingClientRect=()=>box(350,40,272,146);screen.getByRole('article',{name:'项目总纲'}).getBoundingClientRect=()=>box(20,40,272,146);
  Object.defineProperty(document,'elementFromPoint',{configurable:true,value:()=>canvas});const plus=screen.getByLabelText('为 A 创建'+kind);plus.getBoundingClientRect=()=>kind==='子级'?box(626,101,24,24):box(474,190,24,24);
  fireEvent.pointerDown(plus,{button:0,pointerId:2,clientX:638,clientY:113});fireEvent.pointerMove(canvas,{pointerId:2,clientX:850,clientY:450});
  expect(screen.getByText(kind==='同级'?'松手创建同级主题 · Esc 取消':'松手创建子主题 · Esc 取消')).toBeInTheDocument();
  const preview=screen.getByLabelText('拉线预览').querySelector('path')!.getAttribute('d')!;expect(preview).toMatch(kind==='子级'?/^M 638 113/:/^M 486 202/);
  fireEvent.pointerUp(canvas,{pointerId:2,clientX:850,clientY:450});const draft=document.querySelector<HTMLElement>('.kc-quick')!;const position={x:parseFloat(draft.style.left),y:parseFloat(draft.style.top)};
  fireEvent.blur(screen.getByLabelText('新主题标题'));
  await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('create_object',expect.objectContaining({title:'新主题',parent_id:kind==='子级'?'a':'root',position,...(kind==='同级'?{after_object_id:'a',nodes:[]}:{})}),expect.any(String)));
 });
 it('preserves a collapsed source branch when its bottom plus creates a sibling',async()=>{
  f.api.scene.mockResolvedValue({...initial,nodes:[node,a,{...a,id:'grand',node_id:'lg',parent_id:'a',title:'后代',x:680}]});show();await ready();const canvas=pick();
  fireEvent.click(screen.getByLabelText('折叠或展开 A'));expect(screen.getByLabelText('折叠或展开 A')).toHaveAttribute('aria-expanded','false');
  const box=(x:number,y:number,w:number,h:number)=>({left:x,top:y,right:x+w,bottom:y+h,x,y,width:w,height:h,toJSON:()=>({})});canvas.getBoundingClientRect=()=>box(0,0,1200,800);screen.getByRole('article',{name:'A',exact:true}).getBoundingClientRect=()=>box(350,40,272,146);
  Object.defineProperty(document,'elementFromPoint',{configurable:true,value:()=>canvas});fireEvent.pointerDown(screen.getByLabelText('为 A 创建同级'),{button:0,pointerId:2,clientX:486,clientY:202});fireEvent.pointerMove(canvas,{pointerId:2,clientX:850,clientY:450});fireEvent.pointerUp(canvas,{pointerId:2,clientX:850,clientY:450});
  expect(screen.getByLabelText('新主题标题')).toBeInTheDocument();expect(screen.getByLabelText('折叠或展开 A')).toHaveAttribute('aria-expanded','false');
 });
 it('hides plus buttons for multiple selection and never offers root siblings',async()=>{
  show();await ready();const canvas=pick();fireEvent.pointerDown(screen.getByRole('heading',{name:'项目总纲'}),{button:0,pointerId:2,shiftKey:true});fireEvent.pointerUp(canvas,{pointerId:2});expect(screen.queryByLabelText('为 A 创建子级')).toBeNull();
  fireEvent.keyDown(canvas,{key:'Escape'});fireEvent.pointerDown(screen.getByRole('heading',{name:'项目总纲'}),{button:0,pointerId:3});fireEvent.pointerUp(canvas,{pointerId:3});expect(screen.getByLabelText('为 项目总纲 创建子级')).toBeInTheDocument();expect(screen.queryByLabelText('为 项目总纲 创建同级')).toBeNull();
 });
});

describe('visual editing tools',()=>{
 const a={...node,id:'a',node_id:'la',parent_id:'root',title:'A',x:350,y:40,canvas_data:{note:'保留',style:{color:'#ff0000',background:'#ffffff',border:'#a8b3c5',font_size:20,bold:true,italic:false,shape:'rounded'}}};
 const b={...node,id:'b',node_id:'lb',parent_id:'root',title:'B',x:350,y:300};
 beforeEach(()=>{vi.stubGlobal('PointerEvent',class extends MouseEvent{pointerId:number;constructor(type:string,init:PointerEventInit={}){super(type,init);this.pointerId=init.pointerId??1;}});f.api.scene.mockResolvedValue({...initial,nodes:[node,a,b]});});
 function pick(title:string,shift=false){const canvas=screen.getByLabelText('知识画布');fireEvent.pointerDown(screen.getByRole('article',{name:title,exact:true}),{button:0,pointerId:1,clientX:400,clientY:150,shiftKey:shift});fireEvent.pointerUp(canvas,{pointerId:1,clientX:400,clientY:150});return canvas;}
 it('applies the format brush only to target style and allows Esc cancellation',async()=>{
  show();await ready();const canvas=pick('A');fireEvent.click(screen.getByRole('button',{name:'格式刷',exact:true}));pick('B');await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_canvas_data',expect.objectContaining({updates:[expect.objectContaining({object_id:'b',canvas_data:{style:a.canvas_data.style,title_format:{align:'left',runs:[{text:'B'}]}}})]}),expect.any(String)));
  await waitFor(()=>expect(screen.queryByText('正在保存…')).toBeNull());pick('A');fireEvent.click(screen.getByRole('button',{name:'格式刷',exact:true}));fireEvent.keyDown(canvas,{key:'Escape'});pick('B');expect(f.api.command).toHaveBeenCalledTimes(1);
 });
 it.each([{metaKey:true,ctrlKey:false},{metaKey:false,ctrlKey:true}])('copies and pastes styles using platform modifier %o',async modifier=>{
  show();await ready();const canvas=pick('A');fireEvent.keyDown(canvas,{key:'c',altKey:true,...modifier});pick('B');fireEvent.keyDown(canvas,{key:'v',altKey:true,...modifier});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_canvas_data',expect.objectContaining({updates:[expect.objectContaining({object_id:'b',canvas_data:{style:a.canvas_data.style,title_format:{align:'left',runs:[{text:'B'}]}}})]}),expect.any(String)));
 });
 it('saves a multiple selection as one style command preserving each note',async()=>{
  show();await ready();pick('A');pick('B',true);fireEvent.click(screen.getByRole('button',{name:'样式',exact:true}));changeField(screen.getByLabelText('背景颜色'),{target:{value:'#00ff00'}});fireEvent.click(screen.getByRole('button',{name:'保存修改'}));await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_canvas_data',expect.objectContaining({updates:[expect.objectContaining({object_id:'a',canvas_data:expect.objectContaining({note:'保留',style:expect.objectContaining({background:'#00ff00'})})}),expect.objectContaining({object_id:'b',canvas_data:expect.objectContaining({style:expect.objectContaining({background:'#00ff00'})})})]}),expect.any(String)));
 });
});

describe('inline image insertion',()=>{
 beforeEach(()=>{vi.stubGlobal('PointerEvent',class extends MouseEvent{pointerId:number;constructor(type:string,init:PointerEventInit={}){super(type,init);this.pointerId=init.pointerId??1;}});f.files.upload.mockResolvedValue({success:true,data:{binding_id:'picture'}});f.files.list.mockResolvedValue([{id:'picture',title:'截图',current_revision:2,version:{mime_type:'image/png',name:'shot.png'}}]);});
 it.each(['paste','drop'])('inserts a fixed image version using %s',async kind=>{
  show();await ready();const canvas=screen.getByLabelText('知识画布'),article=screen.getByRole('article',{name:'项目总纲'});fireEvent.pointerDown(article,{button:0,pointerId:1});fireEvent.pointerUp(canvas,{pointerId:1});const file=new File(['png'],'shot.png',{type:'image/png'});
  if(kind==='paste')fireEvent.paste(canvas,{clipboardData:{files:[file],getData:()=>''}});else fireEvent.drop(article,{dataTransfer:{files:[file],types:['Files']}});
  await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_canvas_data',expect.objectContaining({updates:[expect.objectContaining({object_id:'root',canvas_data:{image:{binding_id:'picture',revision:2,width:220,placement:'bottom',aspect:1,alt:'shot.png'}}})]}),expect.any(String)));expect(f.files.upload).toHaveBeenCalledTimes(1);
 });
});

describe('media input shortcuts',()=>{
 beforeEach(()=>{vi.stubGlobal('PointerEvent',class extends MouseEvent{pointerId:number;constructor(type:string,init:PointerEventInit={}){super(type,init);this.pointerId=init.pointerId??1;}});});
 function pickRoot(){const canvas=screen.getByLabelText('知识画布');fireEvent.pointerDown(screen.getByRole('article',{name:'项目总纲'}),{button:0,pointerId:1});fireEvent.pointerUp(canvas,{pointerId:1});return canvas;}
 it.each([{metaKey:true},{ctrlKey:true}])('preserves native URL input undo on %o',async modifier=>{
  show();await ready();pickRoot();fireEvent.click(screen.getByRole('button',{name:'网页与视频链接',exact:true}));const input=screen.getByLabelText('节点网址');changeField(input,{target:{value:'https://example.com/help'}});fireEvent.keyDown(input,{key:'z',...modifier});expect(f.api.command).not.toHaveBeenCalled();fireEvent.click(screen.getByText('保存链接'));await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_canvas_data',expect.objectContaining({updates:[expect.objectContaining({object_id:'root',canvas_data:{link:{url:'https://example.com/help',kind:'auto',title:''}}})]}),expect.any(String)));
 });
});

describe('topic structure submenu',()=>{
 async function choices(target?:HTMLElement){fireEvent.contextMenu(target??screen.getByLabelText('知识画布'),{clientX:1000,clientY:400});const trigger=await screen.findByRole('menuitem',{name:target?'修改排列类型':'创建主题',exact:true});fireEvent.keyDown(trigger,{key:'ArrowRight'});await screen.findByRole('menuitem',{name:/逻辑图（向右）/});return trigger;}
 it.each(['逻辑图（向左）','思维导图（左右）','组织结构图（向下）','组织结构图（向上）'])('creates only one editable blank topic for %s',async title=>{
  show();await ready();await choices();expect(screen.queryByRole('menuitem',{name:'年度总结'})).toBeNull();fireEvent.click(screen.getByRole('menuitem',{name:title,exact:true}));const input=await screen.findByLabelText('新主题标题');expect(fieldValue(input)).toBe('');changeField(input,{target:{value:'我的主题'}});fireEvent.keyDown(input,{key:'Enter'});await waitFor(()=>expect(f.api.command).toHaveBeenCalledTimes(1));const [operation,payload]=f.api.command.mock.calls[0];expect(operation).toBe('create_template');expect(payload.parent_id).toBeNull();expect(payload.topics).toHaveLength(1);expect(payload.topics[0]).toMatchObject({title:'我的主题',position:{x:940,y:305}});expect(payload.topics[0].structure).toBe({'逻辑图（向左）':'left','思维导图（左右）':'balanced','组织结构图（向下）':'down','组织结构图（向上）':'up'}[title]);expect(screen.queryByRole('dialog')).toBeNull();
 });
 it('modifies the clicked existing topic type together with positions and keeps all metadata',async()=>{
  const child={...node,id:'child',node_id:'child-placement',parent_id:'root',title:'子主题',x:400,y:50};f.api.scene.mockResolvedValue({...initial,nodes:[{...node,canvas_data:{note:'保留正文备注'}},child]});show();await ready();await choices(screen.getByRole('article',{name:'项目总纲'}));expect(screen.queryByRole('menuitem',{name:'创建主题',exact:true})).toBeNull();fireEvent.click(screen.getByRole('menuitem',{name:'组织结构图（向下）',exact:true}));await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_canvas_data',expect.objectContaining({updates:[{object_id:'root',expected_layout_revision:1,canvas_data:{note:'保留正文备注',structure:'down'}}],nodes:[expect.objectContaining({node_id:'child-placement',position:expect.objectContaining({y:168})})]}),expect.any(String)));
 });
 it('keeps double-click creation on the existing default path',async()=>{
  show();await ready();fireEvent.doubleClick(screen.getByLabelText('知识画布'),{clientX:1000,clientY:400});const input=await screen.findByLabelText('新主题标题');changeField(input,{target:{value:'默认'}});fireEvent.keyDown(input,{key:'Enter'});await waitFor(()=>expect(f.api.command.mock.calls[0][0]).toBe('create_object'));expect(f.api.command.mock.calls[0][1]).not.toHaveProperty('structure');
 });
 it('uses left/Esc to return from structure choices before closing the root menu',async()=>{
  show();await ready();const trigger=await choices();fireEvent.keyDown(screen.getByRole('menuitem',{name:/逻辑图（向右）/}),{key:'ArrowLeft'});await waitFor(()=>expect(screen.queryByRole('menuitem',{name:/逻辑图（向右）/})).toBeNull());expect(trigger).toHaveFocus();fireEvent.keyDown(trigger,{key:'ArrowRight'});fireEvent.keyDown(await screen.findByRole('menuitem',{name:/逻辑图（向右）/}),{key:'Escape'});await waitFor(()=>expect(trigger).toHaveFocus());expect(screen.getByRole('menu',{name:'画布右键菜单'})).toBeInTheDocument();
 });
 it.each([{metaKey:true},{ctrlKey:true}])('undoes and redoes a type change through %o shortcuts',async modifier=>{
  let stage=0;let current=structuredClone(initial) as typeof initial & {nodes:({canvas_data?:{structure:string}}&typeof node)[]};
  f.api.scene.mockImplementation(async()=>structuredClone(current));f.api.history.mockImplementation(async()=>({undo:stage===1?{history_id:'structure-step',operation:'update_canvas_data',container_id:'p',can_apply:true}:null,redo:stage===2?{history_id:'structure-step',operation:'update_canvas_data',container_id:'p',can_apply:true}:null}));
  f.api.command.mockImplementation(async(op,p)=>{stage=op==='undo_canvas'?2:1;current={...initial,nodes:[{...node,...(stage===1?{canvas_data:{structure:p.updates?.[0]?.canvas_data.structure??'left'}}:{})}]};return{success:true,data:{object_id:null}};});
  show();await ready();await choices(screen.getByRole('article',{name:'项目总纲'}));fireEvent.click(screen.getByRole('menuitem',{name:'逻辑图（向左）',exact:true}));await waitFor(()=>expect(screen.getByRole('button',{name:'撤销 排列、节点信息与样式'})).toBeEnabled());const canvas=screen.getByLabelText('知识画布');fireEvent.keyDown(canvas,{key:'z',...modifier});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('undo_canvas',{history_id:'structure-step'},expect.any(String)));await waitFor(()=>expect(screen.getByRole('button',{name:'重做 排列、节点信息与样式'})).toBeEnabled());expect(current.nodes[0].canvas_data).toBeUndefined();fireEvent.keyDown(canvas,{key:'z',shiftKey:true,...modifier});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('redo_canvas',{history_id:'structure-step'},expect.any(String)));expect(current.nodes[0].canvas_data?.structure).toBe('left');
 });
 it('does not allow structure modification in a read-only space',async()=>{
  f.api.scene.mockResolvedValue({...initial,containers:initial.containers.map(c=>({...c,role:'viewer'}))});show();await ready();fireEvent.contextMenu(screen.getByRole('article',{name:'项目总纲'}),{clientX:1000,clientY:400});expect(screen.queryByRole('menuitem',{name:'修改排列类型',exact:true})).toBeNull();expect(f.api.command).not.toHaveBeenCalled();
 });
 it('preserves the exact receipt when a structure update result is unknown',async()=>{
  f.api.command.mockRejectedValueOnce(new Error('断网')).mockResolvedValue({success:true,data:{object_id:null}});show();await ready();await choices(screen.getByRole('article',{name:'项目总纲'}));fireEvent.click(screen.getByRole('menuitem',{name:'逻辑图（向左）',exact:true}));await screen.findByRole('alert');const id=f.api.command.mock.calls[0][2];fireEvent.click(screen.getByRole('button',{name:'刷新',exact:true}));await waitFor(()=>expect(f.api.command).toHaveBeenCalledTimes(2));expect(f.api.command.mock.calls[1][2]).toBe(id);
 });
 it.each([{metaKey:true},{ctrlKey:true}])('new children inherit their parent structure with %o keyboard input',async modifier=>{
  f.api.scene.mockResolvedValue({...initial,nodes:[{...node,canvas_data:{structure:'left'}}]});show();await ready();const canvas=screen.getByLabelText('知识画布');fireEvent.pointerDown(screen.getByRole('article',{name:'项目总纲'}),{button:0});fireEvent.pointerUp(canvas);fireEvent.keyDown(canvas,{key:'Tab'});const input=await screen.findByLabelText('新主题标题');changeField(input,{target:{value:'新子主题'}});fireEvent.keyDown(input,{key:'z',...modifier});expect(f.api.command).not.toHaveBeenCalled();fireEvent.keyDown(input,{key:'Enter'});await waitFor(()=>expect(f.api.command).toHaveBeenCalled());const payload=f.api.command.mock.calls[0][1];expect(payload.parent_id).toBe('root');expect(payload.topics[0].position.x).toBeLessThan(node.x);
 });
});

 describe('direction-aware page interactions',()=>{
 beforeEach(()=>{vi.stubGlobal('PointerEvent',class extends MouseEvent{pointerId:number;constructor(type:string,init:PointerEventInit={}){super(type,init);this.pointerId=init.pointerId??1;}});});
 it.each(['left','down','up']as const)('renders creation and folding controls on the %s edge',async structure=>{
  const child={...node,id:'child',node_id:'child-place',title:'子主题',parent_id:'root',x:400,y:400};f.api.scene.mockResolvedValue({...initial,nodes:[{...node,canvas_data:{structure}},child]});show();await ready();const canvas=screen.getByLabelText('知识画布');fireEvent.pointerDown(screen.getByRole('article',{name:'项目总纲'}),{button:0,pointerId:1});fireEvent.pointerUp(canvas,{pointerId:1});const plus=screen.getByRole('button',{name:'为 项目总纲 创建子级'}),fold=screen.getByRole('button',{name:'折叠或展开 项目总纲'});expect(plus.style.left).toContain(structure==='left'?'0%':'50%');expect(fold.style.top).toContain(structure==='up'?'0%':structure==='down'?'100%':'50%');
 });
 it.each([{metaKey:true},{ctrlKey:true}])('pastes into a left structure with one history command on %o',async modifier=>{
  f.api.scene.mockResolvedValue({...initial,nodes:[{...node,canvas_data:{structure:'left'}}]});show();await ready();const canvas=screen.getByLabelText('知识画布');fireEvent.pointerDown(screen.getByRole('article',{name:'项目总纲'}),{button:0,pointerId:1});fireEvent.pointerUp(canvas,{pointerId:1});const transfer={setData:vi.fn()};fireEvent.copy(canvas,{clipboardData:transfer});const record=JSON.parse(transfer.setData.mock.calls.find(c=>c[0]==='application/x-everydayai-branches')![1]);await snapshotSettled();fireEvent.keyDown(canvas,{key:'v',...modifier});fireEvent.paste(canvas,{clipboardData:{files:[],getData:(format:string)=>format==='application/x-everydayai-branches'?JSON.stringify(record):''}});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('copy_branches',expect.objectContaining({parent_id:'root',snapshot_positions:[{source_object_id:'root',position:expect.objectContaining({x:expect.any(Number)})}]}),expect.any(String)));expect(f.api.command.mock.calls[0][1].snapshot_positions[0].position.x).toBeLessThan(node.x);expect(f.api.command).toHaveBeenCalledTimes(1);
 });
 });

it('returns range editing focus to the canvas so undo works immediately',async()=>{
 const a={...node,id:'a',node_id:'pa',title:'A',parent_id:'root',sort_order:1,x:0,y:300},b={...a,id:'b',node_id:'pb',title:'B',sort_order:2,x:300},c={...a,id:'c',node_id:'pc',title:'C',sort_order:3,x:600},summary={...a,id:'s',node_id:'ps',title:'汇总',sort_order:4,x:300,y:650,canvas_data:{summary:{title:'汇总',members:['a','b']}}};f.api.scene.mockResolvedValue({...initial,nodes:[{...node,canvas_data:{structure:'down'}},a,b,c,summary]});show();await ready();fireEvent.click(screen.getByRole('button',{name:'概要括号 汇总'}));const end=screen.getByRole('button',{name:'调整概要结束范围 汇总'});end.focus();fireEvent.keyDown(end,{key:'ArrowRight'});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_canvas_data',expect.objectContaining({updates:[expect.objectContaining({object_id:'s',canvas_data:{summary:{title:'汇总',members:['a','b','c']}}})]}),expect.any(String)));await waitFor(()=>expect(screen.getByLabelText('知识画布')).toHaveFocus());
});

describe('direct typing, parent insertion and outline entry',()=>{
 beforeEach(()=>{vi.stubGlobal('PointerEvent',class extends MouseEvent{pointerId:number;constructor(type:string,init:PointerEventInit={}){super(type,init);this.pointerId=init.pointerId??1;}});});
 function pick(){const canvas=screen.getByLabelText('知识画布');fireEvent.pointerDown(screen.getByRole('article',{name:'项目总纲'}),{button:0,pointerId:1});fireEvent.pointerUp(canvas,{pointerId:1});return canvas;}
 it('keeps the first typed character and subsequent letters in the rename input',async()=>{const user=userEvent.setup();show();await ready();pick();await user.keyboard('Ab');const input=screen.getByLabelText('节点标题');expect(fieldValue(input)).toBe('Ab');expect(input).toHaveFocus();await user.keyboard('{Enter}');await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_title',expect.objectContaining({object_id:'root',title:'Ab'}),expect.any(String)));});
 it('cancels a typed rename without changing the original topic',async()=>{show();await ready();const canvas=pick();fireEvent.keyDown(canvas,{key:'新'});expect(fieldValue(screen.getByLabelText('节点标题'))).toBe('新');fireEvent.keyDown(screen.getByLabelText('节点标题'),{key:'Escape'});expect(f.api.command).not.toHaveBeenCalled();expect(screen.getByRole('article',{name:'项目总纲'})).toBeInTheDocument();});
 it('supports IME entry and does not save on composition confirmation or lose the first character',async()=>{show();await ready();const canvas=pick();fireEvent.keyDown(canvas,{key:'Process',keyCode:229,isComposing:true});const input=screen.getByLabelText('节点标题');expect(input).toHaveFocus();fireEvent.compositionStart(input);changeField(input,{target:{value:'中文标题'}});fireEvent.keyDown(input,{key:'Enter',keyCode:229,isComposing:true});expect(f.api.command).not.toHaveBeenCalled();fireEvent.compositionEnd(input,{data:'中文标题'});fireEvent.keyDown(input,{key:'Enter'});expect(f.api.command).not.toHaveBeenCalled();await act(async()=>{await new Promise(resolve=>setTimeout(resolve,40));});fireEvent.keyDown(input,{key:'Enter'});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_title',expect.objectContaining({title:'中文标题'}),expect.any(String)));});
 it.each([{metaKey:true},{ctrlKey:true}])('inserts a parent through the platform shortcut %o with one command',async modifier=>{show();await ready();const canvas=pick();fireEvent.keyDown(canvas,{key:'Enter',...modifier});const input=await screen.findByLabelText('父主题标题');expect(input).toHaveFocus();changeField(input,{target:{value:'归纳'}});fireEvent.keyDown(input,{key:'Enter'});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('insert_parent',expect.objectContaining({object_id:'root',expected_revision:1,parent_id:null,topics:[expect.objectContaining({title:'归纳',parent_index:-1})]}),expect.any(String)));expect(f.api.command).toHaveBeenCalledTimes(1);});
 it('keeps title edits made during parent insertion and next saves the wrapper rather than inserting again',async()=>{
  let finish!:(v:unknown)=>void;f.api.command.mockReturnValueOnce(new Promise(resolve=>{finish=resolve;}));show();await ready();const canvas=pick();fireEvent.keyDown(canvas,{key:'Enter',ctrlKey:true});const input=screen.getByLabelText('父主题标题');changeField(input,{target:{value:'先保存'}});fireEvent.keyDown(input,{key:'Enter'});await waitFor(()=>expect(f.api.command).toHaveBeenCalledTimes(1));changeField(input,{target:{value:'保存期间继续修改'}});f.api.scene.mockResolvedValue({...initial,nodes:[{...node,parent_id:'wrapper',revision:2},{...node,id:'wrapper',node_id:'pw',title:'先保存'}]});await act(async()=>{finish({success:true,data:{object_id:'wrapper'}});});const edit=await screen.findByLabelText('节点标题');expect(fieldValue(edit)).toBe('保存期间继续修改');fireEvent.keyDown(edit,{key:'Enter'});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_title',expect.objectContaining({object_id:'wrapper',expected_revision:1,title:'保存期间继续修改'}),expect.any(String)));expect(f.api.command.mock.calls.filter(c=>c[0]==='insert_parent')).toHaveLength(1);
 });
 it('opens parent insertion from context without focus stealing',async()=>{show();await ready();pick();fireEvent.contextMenu(screen.getByRole('article',{name:'项目总纲'}));fireEvent.keyDown(screen.getByRole('menuitem',{name:'创建',exact:true}),{key:'ArrowRight'});fireEvent.click(await screen.findByRole('menuitem',{name:'插入父主题 · Ctrl+Enter'}));await waitFor(()=>expect(screen.getByLabelText('父主题标题')).toHaveFocus());expect(f.api.command).not.toHaveBeenCalled();});
 it('opens parent insertion from toolbar without focus stealing',async()=>{const user=userEvent.setup();show();await ready();pick();const button=screen.getByRole('button',{name:'插入',exact:true});button.focus();await user.keyboard('{ArrowDown}');await user.click(await screen.findByRole('menuitem',{name:'父主题 · ⌘Enter / Ctrl+Enter'}));await waitFor(()=>expect(screen.getByLabelText('父主题标题')).toHaveFocus());expect(f.api.command).not.toHaveBeenCalled();});
 it.each(['left','down','up','right']as const)('creates an indented batch as children following %s',async structure=>{f.api.scene.mockResolvedValue({...initial,nodes:[{...node,canvas_data:{structure}}]});show();await ready();const canvas=pick();fireEvent.paste(canvas,{clipboardData:{files:[],getData:(mime:string)=>mime==='text/plain'?'目标\n  指标\n执行':''}});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('create_topics',expect.objectContaining({parent_id:'root',topics:[expect.objectContaining({title:'目标',parent_index:-1}),expect.objectContaining({title:'指标',parent_index:0}),expect.objectContaining({title:'执行',parent_index:-1})]}),expect.any(String)));const point=f.api.command.mock.calls[0][1].topics[0].position;expect(structure==='left'?point.x<node.x:structure==='up'?point.y<node.y:structure==='down'?point.y>node.y:point.x>node.x).toBe(true);expect(f.api.command).toHaveBeenCalledTimes(1);});
 it('creates multiple independent roots on blank paste and rejects invalid indentation without a partial command',async()=>{show();await ready();const canvas=screen.getByLabelText('知识画布');fireEvent.paste(canvas,{clipboardData:{getData:(mime:string)=>mime==='text/plain'?'A\n    B\n  C':''}});await screen.findByRole('alert');expect(f.api.command).not.toHaveBeenCalled();fireEvent.paste(canvas,{clipboardData:{getData:(mime:string)=>mime==='text/plain'?'A\nB':''}});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('create_topics',expect.objectContaining({parent_id:null,topics:[expect.objectContaining({title:'A',parent_index:-1}),expect.objectContaining({title:'B',parent_index:-1})]}),expect.any(String)));});
 it('preserves native text input paste and does not treat a shortcut as typed rename',async()=>{show();await ready();const canvas=pick();fireEvent.keyDown(canvas,{key:'f',ctrlKey:true});expect(screen.queryByLabelText('节点标题')).toBeNull();fireEvent.keyDown(canvas,{key:'F2'});const input=screen.getByLabelText('节点标题');fireEvent.paste(input,{clipboardData:{getData:()=> 'A\nB'}});expect(f.api.command).not.toHaveBeenCalled();});
 it('keeps an unknown batch result on the same receipt when refreshed',async()=>{f.api.command.mockRejectedValueOnce(new Error('断网')).mockResolvedValue({success:true,data:{object_id:null}});show();await ready();const canvas=pick();fireEvent.paste(canvas,{clipboardData:{getData:(mime:string)=>mime==='text/plain'?'A\nB':''}});await screen.findByRole('alert');const key=f.api.command.mock.calls[0][2];fireEvent.click(screen.getByRole('button',{name:'刷新',exact:true}));await waitFor(()=>expect(f.api.command).toHaveBeenCalledTimes(2));expect(f.api.command.mock.calls[1][2]).toBe(key);});
 it('does not rename or insert in a read-only space',async()=>{f.api.scene.mockResolvedValue({...initial,containers:initial.containers.map(c=>({...c,role:'viewer'}))});show();await ready();const canvas=pick();fireEvent.keyDown(canvas,{key:'a'});fireEvent.keyDown(canvas,{key:'Enter',ctrlKey:true});fireEvent.paste(canvas,{clipboardData:{getData:(mime:string)=>mime==='text/plain'?'A\nB':''}});expect(screen.queryByLabelText('节点标题')).toBeNull();expect(screen.queryByLabelText('父主题标题')).toBeNull();expect(f.api.command).not.toHaveBeenCalled();});
});

describe('multiline, width and structural editing',()=>{
 function pick(title='项目总纲'){const canvas=screen.getByLabelText('知识画布');fireEvent.pointerDown(screen.getByRole('article',{name:title,exact:true}),{button:0,pointerId:1});fireEvent.pointerUp(canvas,{pointerId:1});return canvas;}
 it('appends in ordinary edit, replaces by typing, and preserves manual newlines',async()=>{const user=userEvent.setup();show();await ready();const canvas=pick();fireEvent.keyDown(canvas,{key:'F2'});const input=screen.getByLabelText('节点标题')as HTMLTextAreaElement;expect(input).toHaveAttribute('contenteditable','true');expect(titleSelection(input).start).toBe(4);expect(titleSelection(input).end).toBe(4);await user.keyboard('{Shift>}{Enter}{/Shift}后续');expect(input.textContent).toBe('项目总纲\n后续');expect(f.api.command).not.toHaveBeenCalled();fireEvent.keyDown(input,{key:'Enter'});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_title',expect.objectContaining({title:'项目总纲\n后续'}),expect.any(String)));});
 it.each([{metaKey:true,key:'Backspace'},{ctrlKey:true,key:'Delete'}])('deletes only one topic with platform chord %j',async chord=>{const child={...node,id:'child',node_id:'lc',title:'子主题',parent_id:'root'};f.api.scene.mockResolvedValue({...initial,nodes:[node,child]});show();await ready();fireEvent.keyDown(pick(),chord);await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('delete_topic',expect.objectContaining({object_id:'root',expected_container_revision:12}),expect.any(String)));expect(f.api.command).not.toHaveBeenCalledWith('delete_branches',expect.anything(),expect.anything());});
 it.each(['left','right','up','down','balanced']as const)('sorts by Alt+Up under %s without changing parent',async structure=>{const a={...node,id:'a',node_id:'la',title:'A',parent_id:'root',sort_order:1,x:400,y:100},b={...a,id:'b',node_id:'lb',title:'B',sort_order:2,y:350};f.api.scene.mockResolvedValue({...initial,nodes:[{...node,canvas_data:{structure}},a,b]});show();await ready();fireEvent.keyDown(pick('B'),{key:'ArrowUp',altKey:true});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('move_branch',expect.objectContaining({object_id:'b',target_id:'a',placement:'before'}),expect.any(String)));});
 it('resizes with keyboard as one canvas command and allows restoring default width',async()=>{show();await ready();pick();fireEvent.keyDown(screen.getByRole('slider',{name:'调整节点宽度'}),{key:'ArrowRight'});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_canvas_data',expect.objectContaining({updates:[expect.objectContaining({object_id:'root',canvas_data:expect.objectContaining({width:282})})]}),expect.any(String)));});
 it('does not change structure or width in read-only mode',async()=>{f.api.scene.mockResolvedValue({...initial,containers:initial.containers.map(c=>({...c,role:'viewer'}))});show();await ready();const canvas=pick();fireEvent.keyDown(canvas,{key:'Delete',ctrlKey:true});fireEvent.keyDown(canvas,{key:'ArrowUp',altKey:true});expect(screen.queryByRole('slider')).toBeNull();expect(f.api.command).not.toHaveBeenCalled();});
});

describe('recursive folding and center navigation',()=>{
 const a={...node,id:'a',node_id:'la',title:'A',parent_id:'root',x:400,y:100},b={...a,id:'b',node_id:'lb',title:'B',parent_id:'a',x:750},c={...a,id:'c',node_id:'lc',title:'C',parent_id:'b',x:1100},peer={...a,id:'peer',node_id:'lp',title:'Peer',parent_id:'root',y:400};
 beforeEach(()=>f.api.scene.mockResolvedValue({...initial,nodes:[node,a,b,c,peer]}));
 function pick(title='项目总纲'){const canvas=screen.getByLabelText('知识画布');fireEvent.pointerDown(screen.getByRole('article',{name:title,exact:true}),{button:0,pointerId:1});fireEvent.pointerUp(canvas,{pointerId:1});return canvas;}
 it('folds and unfolds every descendant using toolbar without a mutation command',async()=>{show();await ready();pick();fireEvent.click(screen.getByRole('button',{name:'折叠全部后代',exact:true}));expect(screen.queryByRole('article',{name:'A',exact:true})).toBeNull();fireEvent.click(screen.getByRole('button',{name:'展开全部后代',exact:true}));for(const title of ['A','B','C','Peer'])expect(screen.getByRole('article',{name:title,exact:true})).toBeInTheDocument();expect(f.api.command).not.toHaveBeenCalled();});
 it('limits recursive folding to the selected branch and supports explicit expand shortcuts',async()=>{show();await ready();const canvas=pick('A');fireEvent.keyDown(canvas,{key:'ArrowUp',altKey:true,shiftKey:true});expect(screen.queryByRole('article',{name:'B',exact:true})).toBeNull();expect(screen.getByRole('article',{name:'Peer',exact:true})).toBeInTheDocument();fireEvent.keyDown(canvas,{key:'ArrowDown',altKey:true,shiftKey:true});expect(screen.getByRole('article',{name:'C',exact:true})).toBeInTheDocument();expect(f.api.command).not.toHaveBeenCalled();});
 it('supports browsing on read-only spaces',async()=>{f.api.scene.mockResolvedValue({...initial,containers:initial.containers.map(c=>({...c,role:'viewer'})),nodes:[node,a,b,c]});show();await ready();pick();fireEvent.click(screen.getByRole('button',{name:'折叠全部后代',exact:true}));expect(screen.queryByRole('article',{name:'A',exact:true})).toBeNull();fireEvent.click(screen.getByRole('button',{name:'展开全部后代',exact:true}));expect(screen.getByRole('article',{name:'C',exact:true})).toBeInTheDocument();expect(f.api.command).not.toHaveBeenCalled();});
 it('uses Home to select the current tree center and leaves browser refresh shortcuts alone',async()=>{show();await ready();const canvas=pick('C');fireEvent.keyDown(canvas,{key:'Home'});expect(screen.getByRole('article',{name:'项目总纲'})).toHaveClass('selected');expect(screen.getByRole('article',{name:'C',exact:true})).not.toHaveClass('selected');fireEvent.keyDown(canvas,{key:'r',ctrlKey:true});fireEvent.keyDown(canvas,{key:'r',metaKey:true});expect(f.api.command).not.toHaveBeenCalled();expect(screen.queryByLabelText('节点标题')).toBeNull();});
 it('preserves zoom when centering and respects reduced motion',async()=>{show();await ready();fireEvent.click(screen.getByRole('button',{name:'缩小',exact:true}));const world=document.querySelector('.kc-world')as HTMLElement;const z=world.style.transform.match(/scale\(([^)]+)\)/)?.[1];pick('C');fireEvent.click(screen.getByRole('button',{name:'回到中心主题',exact:true}));expect(world.style.transform.match(/scale\(([^)]+)\)/)?.[1]).toBe(z);expect(document.querySelector('.kc-page')).toHaveAttribute('data-reduced-motion','true');expect(screen.getByRole('article',{name:'项目总纲'})).toHaveClass('selected');});
 it('does not intercept Home or branch browsing shortcuts while editing text',async()=>{show();await ready();const canvas=pick('A');fireEvent.keyDown(canvas,{key:'F2'});const input=screen.getByLabelText('节点标题');fireEvent.keyDown(input,{key:'Home'});fireEvent.keyDown(input,{key:'ArrowUp',altKey:true,shiftKey:true});expect(fieldValue(input)).toBe('A');expect(screen.getByRole('article',{name:'C',exact:true})).toBeInTheDocument();expect(f.api.command).not.toHaveBeenCalled();});
 it('offers recursive actions and center in the read-only context menu',async()=>{f.api.scene.mockResolvedValue({...initial,containers:initial.containers.map(c=>({...c,role:'viewer'})),nodes:[node,a,b]});show();await ready();pick('A');fireEvent.contextMenu(screen.getByRole('article',{name:'A',exact:true}));fireEvent.keyDown(screen.getByRole('menuitem',{name:'分支',exact:true}),{key:'ArrowRight'});fireEvent.click(await screen.findByRole('menuitem',{name:'折叠全部后代 · Alt+Shift+↑'}));expect(screen.queryByRole('article',{name:'B',exact:true})).toBeNull();expect(screen.getByRole('article',{name:'A',exact:true})).toHaveClass('selected');});
 it('keeps root selection visible when folding overlapping selected branches',async()=>{show();await ready();const canvas=pick('C');fireEvent.pointerDown(screen.getByRole('article',{name:'项目总纲'}),{button:0,pointerId:2,shiftKey:true});fireEvent.pointerUp(canvas,{pointerId:2});fireEvent.click(screen.getByRole('button',{name:'折叠全部后代',exact:true}));expect(screen.getByRole('article',{name:'项目总纲'})).toHaveClass('selected');expect(screen.queryByRole('article',{name:'C',exact:true})).toBeNull();expect(screen.getByText(/已选 1 个节点/)).toBeInTheDocument();});
 it('allows recursive browsing and center during a write without issuing another write',async()=>{show();await ready();const canvas=pick('Peer');let finish!:(value:unknown)=>void;f.api.command.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));fireEvent.keyDown(canvas,{key:'Delete'});await waitFor(()=>expect(f.api.command).toHaveBeenCalledTimes(1));pick();fireEvent.click(screen.getByRole('button',{name:'折叠全部后代',exact:true}));expect(screen.queryByRole('article',{name:'A',exact:true})).toBeNull();fireEvent.click(screen.getByRole('button',{name:'展开全部后代',exact:true}));pick('C');fireEvent.keyDown(canvas,{key:'Home'});expect(screen.getByRole('article',{name:'项目总纲'})).toHaveClass('selected');expect(f.api.command).toHaveBeenCalledTimes(1);f.api.scene.mockResolvedValue({...initial,nodes:[node,a,b,c]});await act(async()=>finish({success:true,data:{}}));expect(screen.getByRole('article',{name:'项目总纲'})).toHaveClass('selected');});
 it('can reverse a recursive transition midway with normal animation',async()=>{vi.stubGlobal('matchMedia',vi.fn((query:string)=>({matches:false,media:query,addEventListener:vi.fn(),removeEventListener:vi.fn()})));show();await ready();pick();fireEvent.click(screen.getByRole('button',{name:'折叠全部后代',exact:true}));expect(screen.queryByRole('article',{name:'C',exact:true})).toBeNull();fireEvent.click(screen.getByRole('button',{name:'展开全部后代',exact:true}));await waitFor(()=>expect(screen.getByRole('article',{name:'C',exact:true})).toBeInTheDocument());expect(document.querySelectorAll('[data-departure-id]')).toHaveLength(0);expect(f.api.command).not.toHaveBeenCalled();});

});

describe('free drag alignment and spacing',()=>{
 const source={...node,id:'s',node_id:'ls',title:'自由分支',parent_id:null,x:100,y:100},child={...source,id:'child',node_id:'lc',title:'后代',parent_id:'s',y:300},ref={...node,id:'ref',node_id:'lr',title:'参照节点',parent_id:null,x:500,y:400};
 beforeEach(()=>{class TestPointerEvent extends MouseEvent{pointerId:number;constructor(type:string,options:PointerEventInit={}){super(type,options);this.pointerId=options.pointerId??1;}}vi.stubGlobal('PointerEvent',TestPointerEvent);f.api.scene.mockResolvedValue({...initial,nodes:[source,child,ref]});vi.spyOn(HTMLElement.prototype,'getBoundingClientRect').mockImplementation(()=>({left:0,top:0,width:1400,height:1200,right:1400,bottom:1200,x:0,y:0,toJSON:()=>({})}));});
 afterEach(()=>vi.restoreAllMocks());
 function begin(){const canvas=screen.getByLabelText('知识画布');fireEvent.pointerDown(screen.getByRole('article',{name:'自由分支',exact:true}),{button:0,pointerId:1,clientX:180,clientY:215});return canvas;}
 it('previews snapped positions and saves the whole branch in one existing history command',async()=>{show();await ready();const canvas=begin();fireEvent.pointerMove(canvas,{pointerId:1,clientX:575,clientY:815});expect(screen.getByRole('article',{name:'自由分支'})).toHaveStyle({left:'500px'});expect(document.querySelector('.kc-smart-guides line')).not.toBeNull();expect(f.api.command).not.toHaveBeenCalled();fireEvent.pointerUp(canvas,{pointerId:1,clientX:575,clientY:815});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('move_branch',expect.objectContaining({placement:'free',nodes:expect.arrayContaining([expect.objectContaining({node_id:'ls',position:{x:500,y:700}}),expect.objectContaining({node_id:'lc',position:{x:500,y:900}})])}),expect.any(String)));expect(f.api.command).toHaveBeenCalledTimes(1);expect(document.querySelector('.kc-smart-guides')).toBeNull();});
 it('allows Shift to disable snapping in place and saves the raw point when released with Shift',async()=>{show();await ready();const canvas=begin();fireEvent.pointerMove(canvas,{pointerId:1,clientX:575,clientY:815});fireEvent.keyDown(canvas,{key:'Shift',shiftKey:true});expect(screen.getByRole('article',{name:'自由分支'})).toHaveStyle({left:'495px'});expect(document.querySelector('.kc-smart-guides')).toBeNull();fireEvent.keyUp(canvas,{key:'Shift'});expect(screen.getByRole('article',{name:'自由分支'})).toHaveStyle({left:'500px'});fireEvent.pointerUp(canvas,{pointerId:1,clientX:575,clientY:815,shiftKey:true});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('move_branch',expect.objectContaining({nodes:expect.arrayContaining([expect.objectContaining({node_id:'ls',position:{x:495,y:700}})])}),expect.any(String)));});
 it('cancels without writing or retaining a guide',async()=>{show();await ready();const canvas=begin();fireEvent.pointerMove(canvas,{pointerId:1,clientX:575,clientY:815});fireEvent.keyDown(canvas,{key:'Escape'});expect(document.querySelector('.kc-smart-guides')).toBeNull();expect(screen.getByRole('article',{name:'自由分支'})).toHaveStyle({left:'100px'});expect(f.api.command).not.toHaveBeenCalled();});
 it('prioritizes a structural target over free alignment',async()=>{show();await ready();const canvas=begin();fireEvent.pointerMove(canvas,{pointerId:1,clientX:615,clientY:519});expect(document.querySelector('.kc-smart-guides')).toBeNull();fireEvent.pointerUp(canvas,{pointerId:1,clientX:615,clientY:519});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('move_branch',expect.objectContaining({target_id:'ref',placement:'child'}),expect.any(String)));});
 it('never starts alignment for a read-only node',async()=>{f.api.scene.mockResolvedValue({...initial,containers:initial.containers.map(c=>({...c,role:'viewer'})),nodes:[source,ref]});show();await ready();const canvas=begin();fireEvent.pointerMove(canvas,{pointerId:1,clientX:575,clientY:815});fireEvent.pointerUp(canvas,{pointerId:1,clientX:575,clientY:815});expect(document.querySelector('.kc-smart-guides')).toBeNull();expect(f.api.command).not.toHaveBeenCalled();});
 it('translates multiple selected branches as a rigid group',async()=>{const second={...source,id:'second',node_id:'lsecond',title:'第二分支',y:500};f.api.scene.mockResolvedValue({...initial,nodes:[source,child,second,ref]});show();await ready();const canvas=screen.getByLabelText('知识画布');fireEvent.pointerDown(screen.getByRole('article',{name:'自由分支'}),{button:0,pointerId:1});fireEvent.pointerUp(canvas,{pointerId:1});fireEvent.pointerDown(screen.getByRole('article',{name:'第二分支'}),{button:0,pointerId:1,ctrlKey:true});fireEvent.pointerUp(canvas,{pointerId:1});begin();fireEvent.pointerMove(canvas,{pointerId:1,clientX:575,clientY:815});expect(screen.getByRole('article',{name:'第二分支'})).toHaveStyle({left:'500px',top:'1100px'});fireEvent.pointerUp(canvas,{pointerId:1,clientX:575,clientY:815});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('move_branches',expect.objectContaining({placement:'free',nodes:expect.arrayContaining([expect.objectContaining({node_id:'ls',position:{x:500,y:700}}),expect.objectContaining({node_id:'lsecond',position:{x:500,y:1100}})])}),expect.any(String)));});
 it('restores snapped coordinates through Mac undo and Windows redo',async()=>{const before={...initial,nodes:[source,child,ref]},after={...initial,nodes:[{...source,x:500,y:700},{...child,x:500,y:900},ref]},entry={history_id:'snap-step',operation:'move_branch',container_id:'p',can_apply:true,reason:''};f.api.command.mockImplementation(async(op)=>{if(op==='move_branch'||op==='redo_canvas'){f.api.scene.mockResolvedValue(after);f.api.history.mockResolvedValue({undo:entry,redo:null});}else if(op==='undo_canvas'){f.api.scene.mockResolvedValue(before);f.api.history.mockResolvedValue({undo:null,redo:entry});}return{success:true,data:{object_id:null}};});show();await ready();const canvas=begin();fireEvent.pointerMove(canvas,{pointerId:1,clientX:575,clientY:815});fireEvent.pointerUp(canvas,{pointerId:1,clientX:575,clientY:815});await waitFor(()=>expect(screen.getByRole('button',{name:'撤销 分支调整'})).toBeEnabled());fireEvent.keyDown(canvas,{key:'z',metaKey:true});await waitFor(()=>expect(screen.getByRole('article',{name:'自由分支'})).toHaveStyle({left:'100px',top:'100px'}));fireEvent.keyDown(canvas,{key:'y',ctrlKey:true});await waitFor(()=>expect(screen.getByRole('article',{name:'自由分支'})).toHaveStyle({left:'500px',top:'700px'}));expect(f.api.command.mock.calls.map(c=>c[0])).toEqual(['move_branch','undo_canvas','redo_canvas']);});
 it('keeps snapping static and functional with normal motion enabled',async()=>{vi.stubGlobal('matchMedia',vi.fn((query:string)=>({matches:false,media:query,addEventListener:vi.fn(),removeEventListener:vi.fn()})));show();await ready();const canvas=begin();fireEvent.pointerMove(canvas,{pointerId:1,clientX:575,clientY:815});expect(screen.getByRole('article',{name:'自由分支'})).toHaveStyle({left:'500px'});expect(document.querySelector('.kc-smart-guides')).toHaveAttribute('aria-hidden','true');fireEvent.keyDown(canvas,{key:'Escape'});expect(document.querySelector('.kc-smart-guides')).toBeNull();});

 it('ignores folded descendants and offscreen reference cards',async()=>{const closed={...ref,id:'closed',node_id:'lclosed',title:'折叠参照组',x:3000},hiddenRef={...ref,parent_id:'closed'};f.api.scene.mockResolvedValue({...initial,nodes:[source,child,closed,hiddenRef]});show();await ready();fireEvent.click(screen.getByRole('button',{name:'折叠或展开 折叠参照组'}));const canvas=begin();fireEvent.pointerMove(canvas,{pointerId:1,clientX:575,clientY:815});expect(screen.getByRole('article',{name:'自由分支'})).toHaveStyle({left:'495px'});expect(document.querySelector('.kc-smart-guides line')).toBeNull();fireEvent.keyDown(canvas,{key:'Escape'});});
 it('restores the unsnapped original branch on a rejected save',async()=>{show();await ready();const canvas=begin();f.api.command.mockRejectedValueOnce(Object.assign(new Error('版本冲突'),{status:409}));fireEvent.pointerMove(canvas,{pointerId:1,clientX:575,clientY:815});fireEvent.pointerUp(canvas,{pointerId:1,clientX:575,clientY:815});await waitFor(()=>expect(screen.getByRole('article',{name:'自由分支'})).toHaveStyle({left:'100px',top:'100px'}));expect(document.querySelector('.kc-smart-guides')).toBeNull();expect(f.api.command).toHaveBeenCalledTimes(1);});

});

describe('whole map style settings',()=>{
 const child={...node,id:'child',node_id:'lc',title:'子主题',parent_id:'root',x:400,y:40},peer={...child,id:'peer',node_id:'lp',title:'同层主题',y:300};
 beforeEach(()=>f.api.scene.mockResolvedValue({...initial,nodes:[node,child,peer]}));
 it('applies map palette, background and fonts as one space-scoped command',async()=>{show();await ready();fireEvent.click(screen.getByRole('button',{name:'全图样式',exact:true}));const dialog=screen.getByRole('dialog',{name:'全图样式'});changeField(within(dialog).getByLabelText('整图配色'),{target:{value:'forest'}});changeField(within(dialog).getByLabelText('全局字体'),{target:{value:'serif'}});fireEvent.click(within(dialog).getByLabelText('背景跟随应用'));changeField(within(dialog).getByLabelText('画布背景'),{target:{value:'#123456'}});fireEvent.click(within(dialog).getByRole('button',{name:'应用全图样式'}));await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_map_theme',expect.objectContaining({container_id:'p',expected_container_revision:12,theme:expect.objectContaining({palette:'forest',font:'serif',background:'#123456'}),updates:[]}),expect.any(String)));expect(f.api.command).toHaveBeenCalledTimes(1);});
 it('saves all automatic layout settings and positions together without moving the center or clearing styles',async()=>{
  f.api.scene.mockResolvedValue({...initial,nodes:[{...node,canvas_data:{structure:'balanced'}},{...child,canvas_data:{width:420}},peer]});
  show();await ready();fireEvent.click(screen.getByRole('button',{name:'全图样式',exact:true}));
  for(const label of ['按左右子树实际高度自动平衡','整图紧凑布局','同层等宽'])fireEvent.click(screen.getByLabelText(label));
  fireEvent.click(screen.getByRole('button',{name:'应用全图样式'}));
  await waitFor(()=>expect(f.api.command).toHaveBeenCalledTimes(1));
  const [op,payload]=f.api.command.mock.calls[0];expect(op).toBe('update_map_theme');
  expect(payload.theme.layout).toEqual({auto_balance:true,compact:true,equal_width:true});expect(payload.updates).toEqual([]);
  expect(payload.nodes.length).toBeGreaterThan(0);expect(payload.nodes.some((n:{node_id:string})=>n.node_id===node.node_id)).toBe(false);
 });
 it('preserves explicit styles by default and only clears overrides when requested',async()=>{const style={color:'#112233',background:'#ffffff',border:'#445566',font_size:20,bold:false,italic:false,shape:'rounded'};f.api.scene.mockResolvedValue({...initial,nodes:[{...node,canvas_data:{style,note:'保留正文外备注',width:360}},child]});show();await ready();fireEvent.click(screen.getByRole('button',{name:'全图样式',exact:true}));fireEvent.click(screen.getByLabelText('覆盖现有节点自定义样式（保留内容、宽度和文件）'));fireEvent.click(screen.getByRole('button',{name:'应用全图样式'}));await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_map_theme',expect.objectContaining({updates:[{object_id:'root',expected_layout_revision:1,style:null}]}),expect.any(String)));});
 it('updates the selected visual level using its displayed style',async()=>{const style={color:'#112233',background:'#ffffff',border:'#445566',font_size:20,bold:false,italic:false,shape:'rounded'};f.api.scene.mockResolvedValue({...initial,nodes:[node,{...child,canvas_data:{style}},peer]});show();await ready();fireEvent.pointerDown(screen.getByRole('article',{name:'子主题',exact:true}),{button:0});fireEvent.pointerUp(screen.getByLabelText('知识画布'),{pointerId:1});fireEvent.click(screen.getByRole('button',{name:'全图样式',exact:true}));expect(screen.getByText(/第 2 层的 2 个主题/)).toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'更新所选节点所在层级'}));await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_map_theme',expect.objectContaining({theme:expect.objectContaining({levels:[{depth:1,style:expect.objectContaining({color:'#112233',font_size:20})}]}),updates:[{object_id:'child',expected_layout_revision:1,style:null}]}),expect.any(String)));});
 it('renders inherited styles and background without baking them into nodes',async()=>{f.api.scene.mockResolvedValue({...initial,containers:initial.containers.map(c=>c.id==='p'?{...c,canvas_theme:{palette:'ocean',background:'#123456',grid:false,font:'mono',font_size:22,levels:[]}}:c),nodes:[node,child]});show();await ready();expect(screen.getByLabelText('知识画布')).toHaveStyle({backgroundColor:'#123456',backgroundImage:'none'});expect(screen.getByRole('heading',{name:'子主题',exact:true})).toHaveStyle({fontSize:'22px'});expect(f.api.command).not.toHaveBeenCalled();});
 it('keeps the settings and request id when the save result is unknown',async()=>{f.api.command.mockRejectedValueOnce(new Error('断网')).mockResolvedValue({success:true,data:{}});show();await ready();fireEvent.click(screen.getByRole('button',{name:'全图样式',exact:true}));changeField(screen.getByLabelText('整图配色'),{target:{value:'sunset'}});fireEvent.click(screen.getByRole('button',{name:'应用全图样式'}));await screen.findByRole('button',{name:'核对保存结果'});const key=f.api.command.mock.calls[0][2];expect(fieldValue(screen.getByLabelText('整图配色'))).toBe('sunset');fireEvent.click(screen.getByRole('button',{name:'核对保存结果'}));await waitFor(()=>expect(screen.queryByRole('dialog',{name:'全图样式'})).toBeNull());expect(f.api.command.mock.calls[1][2]).toBe(key);});
 it('does not offer writes in a read-only space',async()=>{f.api.scene.mockResolvedValue({...initial,containers:initial.containers.map(c=>({...c,role:'viewer'}))});show();await ready();expect(screen.getByRole('button',{name:'全图样式',exact:true})).toBeDisabled();expect(f.api.command).not.toHaveBeenCalled();});
});

it('restores map theme drafts only after rechecking the space permission',async()=>{const view=show();await ready();fireEvent.click(screen.getByRole('button',{name:'全图样式',exact:true}));changeField(screen.getByLabelText('全局字体'),{target:{value:'serif'}});await waitFor(()=>expect(Object.keys(localStorage).some(k=>k.startsWith('everydayai:canvas-drafts:v2:')&&localStorage.getItem(k)?.includes('serif'))).toBe(true));view.unmount();show();await screen.findByRole('dialog',{name:'恢复未保存草稿'});fireEvent.click(screen.getByRole('button',{name:'恢复草稿',exact:true}));await screen.findByRole('dialog',{name:'全图样式'});expect(fieldValue(screen.getByLabelText('全局字体'))).toBe('serif');expect(f.api.command).not.toHaveBeenCalled();});

it('undoes and redoes the entire theme on Mac and Windows while preserving node identity',async()=>{const theme={palette:'forest',background:'#f1f8f1',grid:true,font:'serif',font_size:20,levels:[],layout:{auto_balance:true,compact:true,equal_width:true}},entry={history_id:'theme-step',container_id:'p',operation:'update_map_theme',can_apply:true,reason:''},after={...initial,containers:initial.containers.map(c=>c.id==='p'?{...c,canvas_theme:theme}:c)};f.api.command.mockImplementation(async(op)=>{if(op==='update_map_theme'||op==='redo_canvas'){f.api.scene.mockResolvedValue(after);f.api.history.mockResolvedValue({undo:entry,redo:null});}else if(op==='undo_canvas'){f.api.scene.mockResolvedValue(initial);f.api.history.mockResolvedValue({undo:null,redo:entry});}return{success:true,data:{object_id:null}};});show();await ready();fireEvent.click(screen.getByRole('button',{name:'全图样式',exact:true}));changeField(screen.getByLabelText('整图配色'),{target:{value:'forest'}});fireEvent.click(screen.getByRole('button',{name:'应用全图样式'}));await waitFor(()=>expect(screen.queryByRole('dialog',{name:'全图样式'})).toBeNull());expect(screen.getByRole('heading',{name:'项目总纲'})).toHaveStyle({fontSize:'20px'});const canvas=screen.getByLabelText('知识画布');fireEvent.keyDown(canvas,{key:'z',metaKey:true});await waitFor(()=>expect(screen.getByRole('heading',{name:'项目总纲'}).style.fontSize).toBe(''));fireEvent.keyDown(canvas,{key:'y',ctrlKey:true});await waitFor(()=>expect(screen.getByRole('heading',{name:'项目总纲'})).toHaveStyle({fontSize:'20px'}));expect(f.api.command.mock.calls.map(c=>c[0])).toEqual(['update_map_theme','undo_canvas','redo_canvas']);});

describe('partial title formatting and alignment',()=>{
 function edit(){const canvas=screen.getByLabelText('知识画布');fireEvent.pointerDown(screen.getByRole('article',{name:'项目总纲'}),{button:0,clientX:40,clientY:60});fireEvent.pointerUp(canvas);fireEvent.keyDown(canvas,{key:'F2'});return screen.getByLabelText('节点标题') as HTMLTextAreaElement;}
 it.each([{metaKey:true},{ctrlKey:true}])('formats the selected substring with platform keyboard shortcuts and saves atomically %o',async modifier=>{show();await ready();const input=edit();restoreTitleSelection(input,{start:0,end:2});fireEvent.keyDown(input,{key:'b',...modifier});fireEvent.click(screen.getByLabelText('标题居中'));expect(f.api.command).not.toHaveBeenCalled();fireEvent.keyDown(input,{key:'Enter'});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_title',expect.objectContaining({object_id:'root',title:'项目总纲',expected_container_revision:12,title_format:{align:'center',runs:[{text:'项目',bold:true},{text:'总纲'}]}}),expect.any(String)));});
 it('undoes formatting locally and keeps text selection through toolbar pointer interaction',async()=>{show();await ready();const input=edit();restoreTitleSelection(input,{start:0,end:2});fireEvent.pointerDown(screen.getByLabelText('选中文字加粗'));fireEvent.click(screen.getByLabelText('选中文字加粗'));expect(screen.getByLabelText('节点标题').querySelector('span')).toHaveStyle({fontWeight:700});fireEvent.keyDown(input,{key:'z',ctrlKey:true});expect(screen.getByLabelText('节点标题').querySelector('span')).not.toHaveStyle({fontWeight:700});fireEvent.keyDown(input,{key:'y',ctrlKey:true});expect(screen.getByLabelText('节点标题').querySelector('span')).toHaveStyle({fontWeight:700});fireEvent.keyDown(input,{key:'Escape'});expect(f.api.command).not.toHaveBeenCalled();});
 it('preserves later formatting during an in-flight save',async()=>{let finish!:(v:unknown)=>void;f.api.command.mockReturnValueOnce(new Promise(resolve=>{finish=resolve;}));show();await ready();const input=edit();restoreTitleSelection(input,{start:0,end:2});fireEvent.click(screen.getByLabelText('选中文字加粗'));fireEvent.keyDown(input,{key:'Enter'});await waitFor(()=>expect(f.api.command).toHaveBeenCalledTimes(1));fireEvent.click(screen.getByLabelText('标题右对齐'));await act(async()=>finish({success:true,data:{object_id:'root'}}));expect(screen.getByLabelText('节点标题')).toBeInTheDocument();fireEvent.keyDown(screen.getByLabelText('节点标题'),{key:'Enter'});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_title',expect.objectContaining({expected_revision:2,title_format:expect.objectContaining({align:'right'})}),expect.any(String)));});
});

it('keeps later alignment when an unknown title-format save is reconciled',async()=>{f.api.command.mockRejectedValueOnce(new Error('断网')).mockResolvedValue({success:true,data:{object_id:'root'}});show();await ready();fireEvent.doubleClick(screen.getByRole('article',{name:'项目总纲'}));const input=screen.getByLabelText('节点标题') as HTMLTextAreaElement;restoreTitleSelection(input,{start:0,end:2});fireEvent.click(screen.getByLabelText('选中文字加粗'));fireEvent.keyDown(input,{key:'Enter'});await screen.findAllByRole('alert');const first=f.api.command.mock.calls[0];fireEvent.click(screen.getByLabelText('标题右对齐'));f.api.scene.mockResolvedValue({...initial,nodes:[{...node,revision:2,canvas_data:{title_format:first[1].title_format}}]});fireEvent.click(screen.getByTitle('刷新'));await waitFor(()=>expect(f.api.command).toHaveBeenCalledTimes(2));expect(f.api.command.mock.calls[1]).toEqual(first);expect(fieldValue(await screen.findByLabelText('节点标题'))).toBe('项目总纲');expect(screen.getByLabelText('标题右对齐')).toHaveAttribute('aria-pressed','true');});

it('keeps alignment but drops old text ranges when typing replaces the selected title',async()=>{f.api.scene.mockResolvedValue({...initial,nodes:[{...node,canvas_data:{title_format:{align:'right',runs:[{text:'项目总纲',bold:true}]}}}]});show();await ready();const canvas=screen.getByLabelText('知识画布');fireEvent.pointerDown(screen.getByRole('article',{name:'项目总纲'}),{button:0,clientX:40,clientY:60});fireEvent.pointerUp(canvas);fireEvent.keyDown(canvas,{key:'新'});expect(screen.getByLabelText('标题右对齐')).toHaveAttribute('aria-pressed','true');fireEvent.keyDown(screen.getByLabelText('节点标题'),{key:'Enter'});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_title',expect.objectContaining({title:'新',title_format:{align:'right',runs:[{text:'新'}]}}),expect.any(String)));});

describe('independent groups and unified text scopes',()=>{
 const a={...node,id:'a',node_id:'la',title:'A',parent_id:'root',x:350,y:40},b={...node,id:'b',node_id:'lb',title:'B',parent_id:'root',x:350,y:250};
 const first={id:'frame-one',title:'原外框',members:['a','b']},second={id:'frame-two',title:'新外框',members:['a']};
 beforeEach(()=>{vi.stubGlobal('PointerEvent',class extends MouseEvent{pointerId:number;constructor(t:string,o:PointerEventInit={}){super(t,o);this.pointerId=o.pointerId??1;}});});
 function pick(title:string,shift=false){const c=screen.getByLabelText('知识画布');fireEvent.pointerDown(screen.getByRole('article',{name:title,exact:true}),{button:0,pointerId:1,shiftKey:shift});fireEvent.pointerUp(c,{pointerId:1});return c;}
 it('appends a new independent boundary without replacing the existing frame or note',async()=>{f.api.scene.mockResolvedValue({...initial,nodes:[node,{...a,canvas_data:{boundaries:[first],note:'保留'}},b]});show();await ready();pick('A');fireEvent.click(screen.getByLabelText('外框',{selector:'button'}));await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_canvas_data',expect.objectContaining({updates:[expect.objectContaining({object_id:'a',canvas_data:{note:'保留',boundaries:[first,expect.objectContaining({id:expect.any(String),members:['a'],title:'外框'})]}})]}),expect.any(String)));});
 it('selects one frame, exposes only its handles, and deletes it without deleting topics or other frames',async()=>{f.api.scene.mockResolvedValue({...initial,nodes:[node,{...a,canvas_data:{boundaries:[first,second]}},b]});show();await ready();expect(screen.queryByLabelText('调整外框结束范围 A')).toBeNull();fireEvent.click(screen.getByRole('button',{name:'外框 新外框'}));expect(screen.getByRole('button',{name:'外框 新外框'})).toHaveAttribute('aria-pressed','true');expect(screen.getAllByLabelText('调整外框结束范围 A')).toHaveLength(1);fireEvent.keyDown(screen.getByLabelText('知识画布'),{key:'Delete'});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_canvas_data',expect.objectContaining({updates:[expect.objectContaining({object_id:'a',canvas_data:{boundaries:[first]}})]}),expect.any(String)));expect(f.api.command.mock.calls.some(c=>c[0]==='delete_branches')).toBe(false);expect(screen.getByRole('article',{name:'B',exact:true})).toBeInTheDocument();});
 it('edits the selected frame through its own context menu and preserves the other one',async()=>{f.api.scene.mockResolvedValue({...initial,nodes:[node,{...a,canvas_data:{boundaries:[first,second]}},b]});show();await ready();fireEvent.contextMenu(screen.getByRole('button',{name:'外框 新外框'}),{clientX:400,clientY:240});fireEvent.click(await screen.findByRole('menuitem',{name:'修改外框名称'}));changeField(screen.getByLabelText('外框名称'),{target:{value:'只改第二个'}});fireEvent.click(screen.getByRole('button',{name:'保存修改'}));await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_canvas_data',expect.objectContaining({updates:[expect.objectContaining({object_id:'a',canvas_data:{boundaries:[first,{...second,title:'只改第二个'}]}})]}),expect.any(String)));});
 it('deletes the summary independently while retaining original covered branches',async()=>{const summary={...a,id:'sum',node_id:'ls',title:'汇总',x:700,canvas_data:{summary:{members:['a','b'],title:'汇总'}}};f.api.scene.mockResolvedValue({...initial,nodes:[node,a,b,summary]});show();await ready();fireEvent.click(screen.getByRole('button',{name:'概要括号 汇总'}));fireEvent.keyDown(screen.getByLabelText('知识画布'),{key:'Delete'});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('delete_branches',expect.objectContaining({objects:[{object_id:'sum',expected_revision:1}]}),expect.any(String)));expect(screen.getByRole('article',{name:'A',exact:true})).toBeInTheDocument();});
 it('allows readonly frame selection but blocks independent edits and deletion',async()=>{f.api.scene.mockResolvedValue({...initial,containers:initial.containers.map(c=>({...c,role:'viewer'})),nodes:[node,{...a,canvas_data:{boundaries:[first]}},b]});show();await ready();fireEvent.click(screen.getByRole('button',{name:'外框 原外框'}));fireEvent.keyDown(screen.getByLabelText('知识画布'),{key:'Delete'});expect(screen.getByRole('button',{name:'修改名称'})).toBeDisabled();expect(f.api.command).not.toHaveBeenCalled();});
 it('applies one batch alignment command and preserves distinct text ranges and notes',async()=>{const format={align:'left',runs:[{text:'A',color:'#ff0000'}]};f.api.scene.mockResolvedValue({...initial,nodes:[node,{...a,canvas_data:{note:'保留',title_format:format}},b]});show();await ready();pick('A');pick('B',true);fireEvent.pointerDown(screen.getByLabelText('插入'),{button:0,ctrlKey:false,pointerType:'mouse'});fireEvent.click(await screen.findByRole('menuitem',{name:'居中所选主题'}));await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_canvas_data',expect.objectContaining({updates:[expect.objectContaining({object_id:'a',canvas_data:{note:'保留',title_format:{...format,align:'center'}}}),expect.objectContaining({object_id:'b',canvas_data:{title_format:{runs:[],align:'center'}}})]}),expect.any(String)));});
 it('reset removes local text overrides together with node style without removing content metadata',async()=>{f.api.scene.mockResolvedValue({...initial,nodes:[node,{...a,canvas_data:{note:'备注',title_format:{align:'right',runs:[{text:'A',bold:false,color:'#ff0000'}]}}}]});show();await ready();pick('A');fireEvent.pointerDown(screen.getByLabelText('插入'),{button:0,ctrlKey:false,pointerType:'mouse'});fireEvent.click(await screen.findByRole('menuitem',{name:'恢复所选主题默认样式与文字格式'}));await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_canvas_data',expect.objectContaining({updates:[expect.objectContaining({object_id:'a',canvas_data:{note:'备注',style:null}})]}),expect.any(String)));});
});

it('keeps edit and file actions inside standard cards before and after selection',async()=>{
 show();await ready();const card=screen.getByRole('article',{name:'项目总纲'});expect(card.style.width).toBe('272px');
 expect(within(card).getByRole('button',{name:'目录资料',exact:true})).toBeVisible();expect(within(card).getByRole('button',{name:'文件资料',exact:true})).toBeVisible();
 fireEvent.pointerDown(card,{button:0,pointerId:1});fireEvent.pointerUp(screen.getByLabelText('知识画布'),{pointerId:1});expect(card.style.width).toBe('272px');
 expect(screen.getAllByRole('button',{name:'目录资料',exact:true})).toHaveLength(1);expect(screen.getAllByRole('button',{name:'文件资料',exact:true})).toHaveLength(1);
});
it.each(['top','bottom'] as const)('renders %s stored images between title and card actions',async placement=>{
 f.files.content.mockResolvedValue(new Blob(['image'],{type:'image/png'}));
 f.files.content.mockResolvedValue(new Blob(['image'],{type:'image/png'}));f.api.scene.mockResolvedValue({...initial,nodes:[{...node,canvas_data:{image:{binding_id:'picture',revision:1,width:240,aspect:2,placement}}}]});
 show();await ready();const card=screen.getByRole('article',{name:node.title}),title=within(card).getByRole('heading'),image=within(card).getByRole('button',{name:'放大图片 '+node.title}),action=within(card).getByRole('button',{name:'目录资料',exact:true});
 expect(title.compareDocumentPosition(image)&Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();expect(image.compareDocumentPosition(action)&Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();expect(card.style.width).toBe('272px');
});

describe('remaining P1 durable dialog drafts and persisted groups',()=>{
 function selectRoot(){const card=screen.getByRole('article',{name:'项目总纲',exact:true});fireEvent.pointerDown(card,{button:0,pointerId:1});fireEvent.pointerUp(screen.getByLabelText('知识画布'),{pointerId:1});}
 async function reopen(view:ReturnType<typeof show>){view.unmount();show();await ready();fireEvent.click(await screen.findByRole('button',{name:'恢复草稿'}));}
 it.each([{metaKey:true},{ctrlKey:true}])('protects and restores a format-only title draft (%j)',async modifier=>{
  const view=show();await ready();fireEvent.doubleClick(screen.getByRole('article',{name:'项目总纲'}));const input=screen.getByLabelText('节点标题');restoreTitleSelection(input,{start:0,end:4});fireEvent.keyUp(input,{key:'Shift'});fireEvent.keyDown(input,{key:'b',...modifier});fireEvent.click(screen.getByLabelText('标题居中'));
  const unload=new Event('beforeunload',{cancelable:true});window.dispatchEvent(unload);expect(unload.defaultPrevented).toBe(true);expect(f.api.command).not.toHaveBeenCalled();await reopen(view);expect(fieldValue(await screen.findByLabelText('节点标题'))).toBe('项目总纲');expect(screen.getByLabelText('标题居中')).toHaveAttribute('aria-pressed','true');fireEvent.keyDown(screen.getByLabelText('节点标题'),{key:'Enter'});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_title',expect.objectContaining({title:'项目总纲',title_format:expect.objectContaining({align:'center',runs:expect.arrayContaining([expect.objectContaining({bold:true})])})}),expect.any(String)));
 });
 it.each([{button:'备注',field:'备注内容',value:'恢复备注'},{button:'标签',field:'标签内容',value:'售后，退款'},{button:'任务',field:'任务负责人',value:'测试负责人'},{button:'网页与视频链接',field:'节点网址',value:'https://example.com/recover'},{button:'样式',field:'背景颜色',value:'#123456'}])('restores the actual $button dialog values after reopening',async({button,field,value})=>{
  const view=show();await ready();selectRoot();if(button==='备注')openNote();else if(button==='标签')openLabels();else fireEvent.click(screen.getByRole('button',{name:button,exact:true}));changeField(await screen.findByLabelText(field),{target:{value}});await reopen(view);expect(fieldValue(await screen.findByLabelText(field))).toBe(value);expect(f.api.command).not.toHaveBeenCalled();
 });
 it('restores image and video metadata with pinned file revisions',async()=>{
  const image={binding_id:'picture',revision:2,width:200,placement:'top' as const,aspect:1},video={binding_id:'video',revision:3,title:'原视频'};f.api.scene.mockResolvedValue({...initial,nodes:[{...node,canvas_data:{image,video}}]});f.files.content.mockResolvedValue(new Blob(['image'],{type:'image/png'}));const view=show();await ready();selectRoot();fireEvent.click(screen.getByRole('button',{name:'节点图片',exact:true}));changeField(screen.getByLabelText('图片宽度'),{target:{value:140}});await reopen(view);expect(fieldValue(await screen.findByLabelText('图片宽度'))).toBe('140');fireEvent.click(screen.getByRole('button',{name:'保存修改'}));await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_canvas_data',expect.objectContaining({updates:[expect.objectContaining({canvas_data:expect.objectContaining({image:expect.objectContaining({revision:2,width:140})})})]}),expect.any(String)));
 });
 it('rechecks dialog permission before revealing stored values',async()=>{
  const view=show();await ready();selectRoot();openNote();changeField(screen.getByLabelText('备注内容'),{target:{value:'隔离草稿'}});view.unmount();f.api.scene.mockResolvedValue({...initial,containers:initial.containers.map(c=>({...c,role:'viewer'}))});show();await ready();fireEvent.click(await screen.findByRole('button',{name:'恢复草稿'}));await waitFor(()=>expect(screen.getAllByRole('alert').some(el=>el.textContent?.includes('无编辑权限'))).toBe(true));expect(screen.queryByLabelText('备注内容')).toBeNull();
 });
 it('retains the original layout version when restoring a stale dialog',async()=>{
  const view=show();await ready();selectRoot();openNote();changeField(screen.getByLabelText('备注内容'),{target:{value:'旧版备注草稿'}});view.unmount();f.api.scene.mockResolvedValue({...initial,nodes:[{...node,layout_revision:9,canvas_data:{note:'其他标签的新备注'}}]});show();await ready();fireEvent.click(await screen.findByRole('button',{name:'恢复草稿'}));expect(fieldValue(await screen.findByLabelText('备注内容'))).toBe('旧版备注草稿');expect(screen.getAllByText(/内容已有新版本/).length).toBeGreaterThan(0);fireEvent.click(screen.getByRole('button',{name:'保存备注'}));await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_canvas_data',expect.objectContaining({updates:[expect.objectContaining({expected_layout_revision:1})]}),expect.any(String)));
 });
 it('folds a real persisted root summary and its children with the covered branch',async()=>{
  const a={...node,id:'a',node_id:'la',parent_id:'root',title:'A',x:400},b={...a,id:'b',node_id:'lb',title:'B',y:300},summary={...node,id:'s',node_id:'ls',parent_id:null,title:'概要节点',x:750,canvas_data:{summary:{members:['a','b'],title:'概要'}}},detail={...node,id:'detail',node_id:'ld',parent_id:'s',title:'概要的后代',x:1000};f.api.scene.mockResolvedValue({...initial,nodes:[node,a,b,summary,detail]});show();await ready();fireEvent.click(screen.getByLabelText('折叠或展开 项目总纲'));expect(screen.queryByRole('article',{name:'概要节点'})).toBeNull();expect(screen.queryByRole('article',{name:'概要的后代'})).toBeNull();fireEvent.click(screen.getByLabelText('折叠或展开 项目总纲'));expect(screen.getByRole('article',{name:'概要节点'})).toBeVisible();expect(screen.getByRole('article',{name:'概要的后代'})).toBeVisible();
 });
});

it('restores the video dialog display name and original pinned revision',async()=>{
 f.api.scene.mockResolvedValue({...initial,nodes:[{...node,canvas_data:{video:{binding_id:'video',revision:3,title:'原视频'}}}]});const view=show();await ready();fireEvent.pointerDown(screen.getByRole('article',{name:'项目总纲'}),{button:0,pointerId:1});fireEvent.pointerUp(screen.getByLabelText('知识画布'),{pointerId:1});fireEvent.click(screen.getByRole('button',{name:'节点视频',exact:true}));changeField(screen.getByLabelText('显示名称'),{target:{value:'待保存的视频名称'}});view.unmount();show();await ready();fireEvent.click(await screen.findByRole('button',{name:'恢复草稿'}));expect(fieldValue(await screen.findByLabelText('显示名称'))).toBe('待保存的视频名称');fireEvent.click(screen.getByRole('button',{name:'保存视频'}));await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_canvas_data',expect.objectContaining({updates:[expect.objectContaining({canvas_data:expect.objectContaining({video:{binding_id:'video',revision:3,title:'待保存的视频名称'}})})]}),expect.any(String)));
});
it('restores one independent boundary dialog without replacing another frame',async()=>{
 const a={...node,id:'a',node_id:'la',parent_id:'root',title:'A',canvas_data:{boundaries:[{id:'one',title:'范围一',members:['a']},{id:'two',title:'范围二',members:['a']}]}};f.api.scene.mockResolvedValue({...initial,nodes:[node,a]});const view=show();await ready();fireEvent.doubleClick(screen.getByRole('button',{name:'外框 范围一',exact:true}));changeField(screen.getByLabelText('外框名称'),{target:{value:'恢复后的范围一'}});view.unmount();show();await ready();fireEvent.click(await screen.findByRole('button',{name:'恢复草稿'}));expect(fieldValue(await screen.findByLabelText('外框名称'))).toBe('恢复后的范围一');fireEvent.click(screen.getByRole('button',{name:'保存修改'}));await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_canvas_data',expect.objectContaining({updates:[expect.objectContaining({object_id:'a',canvas_data:expect.objectContaining({boundaries:[{id:'one',title:'恢复后的范围一',members:['a']},{id:'two',title:'范围二',members:['a']}]})})]}),expect.any(String)));
});
it('restores unsaved relationship text and saves against its original version',async()=>{
 const b={...node,id:'b',node_id:'lb',title:'目标节点',x:500},relation={id:'relation',container_id:'p',source_object_id:'root',target_object_id:'b',relation_type:'related',appearance:{text:'原文字',bend:40}};f.api.scene.mockResolvedValue({...initial,nodes:[node,b],relations:[relation]});const view=show();await ready();fireEvent.click(screen.getByRole('button',{name:'编辑关联线文字'}));changeField(screen.getByLabelText('关联线文字'),{target:{value:'待保存的关联线说明'}});view.unmount();show();await ready();fireEvent.click(await screen.findByRole('button',{name:'恢复草稿'}));expect(fieldValue(await screen.findByLabelText('关联线文字'))).toBe('待保存的关联线说明');fireEvent.click(screen.getByRole('button',{name:'保存',exact:true}));await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_relation',expect.objectContaining({expected_container_revision:12,appearance:expect.objectContaining({text:'待保存的关联线说明',bend:40})}),expect.any(String)));
});
it('keeps dialog drafts isolated from another organization',async()=>{
 const view=show();await ready();fireEvent.pointerDown(screen.getByRole('article',{name:'项目总纲'}),{button:0,pointerId:1});fireEvent.pointerUp(screen.getByLabelText('知识画布'),{pointerId:1});openNote();changeField(screen.getByLabelText('备注内容'),{target:{value:'企业内备注草稿'}});view.unmount();f.auth.currentOrgId='another-organization';show();await ready();expect(screen.queryByRole('button',{name:'恢复草稿'})).toBeNull();expect(screen.queryByText('企业内备注草稿')).toBeNull();
});

describe('compact edit continuity',()=>{
 it.each([{metaKey:true},{ctrlKey:true}])('edits inside the original compact card with local undo and cancel (%j)',async modifier=>{
  show();await ready();const card=screen.getByRole('article',{name:node.title,exact:true}),beforeWidth=card.style.width;
  fireEvent.doubleClick(card);const input=screen.getByLabelText('节点标题');
  expect(card).toContainElement(input);expect(document.querySelector('.kc-quick')).toBeNull();expect(card.style.width).toBe(beforeWidth);
  changeField(input,{target:{value:'多行\n标题内容'}});expect(card).toContainElement(screen.getByLabelText('节点标题'));
  fireEvent.keyDown(input,{key:'z',...modifier});expect(fieldValue(input)).toBe(node.title);
  fireEvent.keyDown(input,{key:'Escape'});expect(screen.queryByLabelText('节点标题')).toBeNull();expect(card.style.width).toBe(beforeWidth);expect(f.api.command).not.toHaveBeenCalled();
 });
 it('preserves annotations and their layout during inline title editing',async()=>{
  f.api.scene.mockResolvedValue({...initial,nodes:[{...node,canvas_data:{note:'旁注',labels:['标签'],task:{progress:50}}}]});show();await ready();const card=screen.getByRole('article',{name:node.title,exact:true});
  fireEvent.doubleClick(card);expect(card).toContainElement(screen.getByLabelText('节点标题'));expect(within(card).getByLabelText('查看备注 '+node.title)).toBeInTheDocument();expect(within(card).getByText('标签')).toBeInTheDocument();
 });
});

it('fits full off-node relation extrema with reduced motion without writing history',async()=>{
 const target={...node,id:'curve-target',node_id:'curve-target',x:450,y:200,title:'曲线终点'},relation={id:'curved',container_id:'p',source_object_id:'root',target_object_id:'curve-target',relation_type:'related',appearance:{source_control:{x:-900,y:-1800},target_control:{x:1100,y:1500}}};
 f.api.scene.mockResolvedValue({...initial,nodes:[node,target],relations:[relation]});show();await ready();const canvas=screen.getByLabelText('知识画布');canvas.getBoundingClientRect=()=>({left:0,top:0,right:1000,bottom:800,width:1000,height:800,x:0,y:0,toJSON:()=>({})});
 fireEvent.click(screen.getByRole('button',{name:'适配全图'}));
 const transform=(document.querySelector('.kc-world') as HTMLElement).style.transform,values=transform.match(/translate\(([-\d.e]+)px,\s*([-\d.e]+)px\) scale\(([-\d.e]+)\)/)!;
 expect(values).not.toBeNull();const [,sx,sy,sz]=values.map(Number),{relationCurve,curveBounds}=await import('../../components/knowledge/relationGeometry'),{topicSize}=await import('../../components/knowledge/topicEditing');
 const bounds=curveBounds(relationCurve({...node,...topicSize(node)},{...target,...topicSize(target)},0,1,relation.appearance));
 expect(bounds.x*sz+sx).toBeGreaterThanOrEqual(0);expect((bounds.x+bounds.width)*sz+sx).toBeLessThanOrEqual(1000);
 expect(bounds.y*sz+sy).toBeGreaterThanOrEqual(0);expect((bounds.y+bounds.height)*sz+sy).toBeLessThanOrEqual(800);expect(f.api.command).not.toHaveBeenCalled();
});

it.each([false,true])('fits line text and conditionally includes selected control handles (selected=%s)',async selected=>{
 const target={...node,id:'target-bounds',node_id:'target-bounds',title:'终点',x:400,y:0},relation={id:'full-bounds',container_id:'p',source_object_id:'root',target_object_id:'target-bounds',relation_type:'related',appearance:{text:'很长的关联说明',source_control:{x:0,y:-2000},target_control:{x:0,y:-2000}}};
 f.api.scene.mockResolvedValue({...initial,nodes:[{...node,x:0,y:0},target],relations:[relation]});show();await ready();const canvas=screen.getByLabelText('知识画布');canvas.getBoundingClientRect=()=>({left:0,top:0,right:1000,bottom:800,width:1000,height:800,x:0,y:0,toJSON:()=>({})});
 const label=document.querySelector('.kc-relation-label') as SVGTextElement;
 Object.defineProperty(label,'getBBox',{configurable:true,value:()=>({x:Number(label.getAttribute('x'))-1500,y:Number(label.getAttribute('y'))-18,width:3000,height:24})});
 if(selected)fireEvent.click(screen.getByRole('button',{name:'关联线：项目总纲 → 终点'}));
 fireEvent.click(screen.getByRole('button',{name:'适配全图'}));
 const values=(document.querySelector('.kc-world') as HTMLElement).style.transform.match(/translate\(([-\d.e]+)px,\s*([-\d.e]+)px\) scale\(([-\d.e]+)\)/)!;const [,x,y,z]=values.map(Number);
 const {relationCurve}=await import('../../components/knowledge/relationGeometry'),{topicSize}=await import('../../components/knowledge/topicEditing'),a={...node,x:0,y:0},curve=relationCurve({...a,...topicSize(a)},{...target,...topicSize(target)},0,1,relation.appearance);
 expect((curve.middle.x-1500)*z+x).toBeGreaterThanOrEqual(0);expect((curve.middle.x+1500)*z+x).toBeLessThanOrEqual(1000);
 if(selected)for(const control of [curve.control1,curve.control2]){expect(control.y*z+y).toBeGreaterThanOrEqual(0);expect(control.y*z+y).toBeLessThanOrEqual(800);}
 expect(f.api.command).not.toHaveBeenCalled();
});

it('reveals keyboard selection below the measured wrapped global toolbar',async()=>{
 show();await ready();const canvas=screen.getByLabelText('知识画布'),toolbar=screen.getByRole('toolbar',{name:'画布操作'});
 canvas.getBoundingClientRect=()=>({x:0,y:0,left:0,top:0,right:800,bottom:700,width:800,height:700,toJSON:()=>({})});
 toolbar.getBoundingClientRect=()=>({x:12,y:56,left:12,top:56,right:788,bottom:220,width:776,height:164,toJSON:()=>({})});
 const card=screen.getByRole('article',{name:node.title,exact:true});fireEvent.pointerDown(card,{button:0,pointerId:1});fireEvent.pointerUp(canvas,{pointerId:1});
 await waitFor(()=>{const values=(document.querySelector('.kc-world') as HTMLElement).style.transform.match(/translate\(([-\d.e]+)px,\s*([-\d.e]+)px\)/)!;expect(Number(values[2])+node.y).toBeGreaterThanOrEqual(232);});
 expect(f.api.command).not.toHaveBeenCalled();
});

describe('P1 complete workflow acceptance regressions',()=>{
 it('keeps the same creation editor and caret when an unknown save is reconciled after more typing',async()=>{
  f.api.command.mockRejectedValueOnce(new Error('应答丢失')).mockResolvedValue({success:true,data:{object_id:'made'}});
  show();await ready();fireEvent.doubleClick(screen.getByLabelText('知识画布'),{clientX:500,clientY:450});const input=screen.getByLabelText('新主题标题');changeField(input,{target:{value:'已发送'}});fireEvent.keyDown(input,{key:'Enter'});await screen.findAllByRole('alert');
  changeField(input,{target:{value:'已发送，继续输入'}});restoreTitleSelection(input,{start:3,end:3});
  f.api.scene.mockResolvedValue({...initial,nodes:[node,{...node,id:'made',node_id:'made',title:'已发送',x:440,y:355}]});fireEvent.click(screen.getByTitle('刷新'));
  await waitFor(()=>expect(f.api.command).toHaveBeenCalledTimes(2));await waitFor(()=>expect(screen.getByLabelText('节点标题')).toBe(input));expect(fieldValue(input)).toBe('已发送，继续输入');expect(titleSelection(input)).toMatchObject({start:3,end:3});
 });
 it('lets another organization edit while an old organization save is still waiting',async()=>{
  let finish!:(value:unknown)=>void;f.api.command.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));const view=show();await ready();fireEvent.doubleClick(screen.getByRole('article',{name:node.title}));changeField(screen.getByLabelText('节点标题'),{target:{value:'原企业慢网保存'}});fireEvent.keyDown(screen.getByLabelText('节点标题'),{key:'Enter'});await waitFor(()=>expect(f.api.command).toHaveBeenCalledTimes(1));
  f.auth.currentOrgId='other-slow-org';f.api.scene.mockResolvedValue({...initial,nodes:[{...node,id:'other',node_id:'other',title:'另一企业主题'}]});view.rerender(<MemoryRouter><KnowledgeCanvas/></MemoryRouter>);const card=await screen.findByRole('article',{name:'另一企业主题'});fireEvent.doubleClick(card);expect(await screen.findByLabelText('节点标题')).toHaveTextContent('另一企业主题');
  await act(async()=>finish({success:true,data:{object_id:'root'}}));expect(screen.getByLabelText('节点标题')).toHaveTextContent('另一企业主题');
 });
});

it('ignores a late response from an earlier session of the same organization while a new save is pending',async()=>{
 let oldDone!:(value:unknown)=>void,newDone!:(value:unknown)=>void;
 f.api.command.mockImplementationOnce(()=>new Promise(resolve=>{oldDone=resolve;})).mockImplementationOnce(()=>new Promise(resolve=>{newDone=resolve;}));
 const view=show();await ready();fireEvent.doubleClick(screen.getByRole('article',{name:node.title}));changeField(screen.getByLabelText('节点标题'),{target:{value:'旧会话提交'}});fireEvent.keyDown(screen.getByLabelText('节点标题'),{key:'Enter'});await waitFor(()=>expect(f.api.command).toHaveBeenCalledTimes(1));
 f.auth.currentOrgId='away';view.rerender(<MemoryRouter><KnowledgeCanvas/></MemoryRouter>);await ready();f.auth.currentOrgId='org';view.rerender(<MemoryRouter><KnowledgeCanvas/></MemoryRouter>);await ready();
 vi.spyOn(window,'confirm').mockReturnValueOnce(true);fireEvent.click(await screen.findByRole('button',{name:'丢弃草稿'}));fireEvent.doubleClick(screen.getByRole('article',{name:node.title}));changeField(screen.getByLabelText('节点标题'),{target:{value:'新会话提交'}});fireEvent.keyDown(screen.getByLabelText('节点标题'),{key:'Enter'});await waitFor(()=>expect(f.api.command).toHaveBeenCalledTimes(2));
 await act(async()=>oldDone({success:true,data:{object_id:'root'}}));expect(within(document.querySelector('header')!).getByRole('status')).toHaveTextContent('正在保存…');fireEvent.keyDown(screen.getByLabelText('节点标题'),{key:'Enter'});expect(f.api.command).toHaveBeenCalledTimes(2);
 await act(async()=>newDone({success:true,data:{object_id:'root'}}));await waitFor(()=>expect(screen.queryByLabelText('节点标题')).toBeNull());
});

it('recovers text typed during an interrupted pending creation and replays the exact receipt before saving later text',async()=>{
 let finish!:(value:unknown)=>void;f.api.command.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;})).mockResolvedValue({success:true,data:{object_id:'made'}});
 const view=show();await ready();fireEvent.doubleClick(screen.getByLabelText('知识画布'),{clientX:500,clientY:450});changeField(screen.getByLabelText('新主题标题'),{target:{value:'发送内容'}});fireEvent.keyDown(screen.getByLabelText('新主题标题'),{key:'Enter'});await waitFor(()=>expect(f.api.command).toHaveBeenCalledTimes(1));const original=f.api.command.mock.calls[0];changeField(screen.getByLabelText('新主题标题'),{target:{value:'发送之后继续输入'}});view.unmount();
 f.api.scene.mockResolvedValue({...initial,nodes:[node,{...node,id:'made',node_id:'made',title:'发送内容'}]});show();await ready();fireEvent.click(await screen.findByRole('button',{name:'恢复草稿'}));const restored=await screen.findByLabelText('新主题标题');expect(fieldValue(restored)).toBe('发送之后继续输入');expect(f.api.command).toHaveBeenCalledTimes(1);
 fireEvent.click(screen.getByTitle('刷新'));await waitFor(()=>expect(f.api.command).toHaveBeenCalledTimes(2));expect(f.api.command.mock.calls[1]).toEqual(original);expect(screen.getByLabelText('节点标题')).toBe(restored);
 fireEvent.keyDown(restored,{key:'Enter'});await waitFor(()=>expect(f.api.command).toHaveBeenCalledTimes(3));expect(f.api.command.mock.calls[2]).toEqual(['update_title',expect.objectContaining({object_id:'made',expected_revision:1,title:'发送之后继续输入'}),expect.any(String)]);await act(async()=>finish({success:true,data:{object_id:'made'}}));
});

it('keeps summary directory folding independent from canvas folding',async()=>{
 const a={...node,id:'member-a',node_id:'member-a',parent_id:'root',title:'成员A'},b={...a,id:'member-b',node_id:'member-b',title:'成员B'},summary={...node,id:'summary-dir',node_id:'summary-dir',parent_id:null,title:'范围概要',canvas_data:{summary:{title:'范围概要',members:['member-a','member-b']}}};
 f.api.scene.mockResolvedValue({...initial,nodes:[node,a,b,summary]});show();await ready();const directory=screen.getByLabelText('知识目录');expect(within(directory).getByRole('button',{name:'范围概要',exact:true})).toBeInTheDocument();
 fireEvent.click(screen.getByLabelText('折叠或展开 '+node.title));expect(within(directory).getByRole('button',{name:'范围概要',exact:true})).toBeInTheDocument();expect(screen.queryByRole('article',{name:'范围概要'})).toBeNull();
 fireEvent.click(within(directory).getByRole('button',{name:'折叠目录 '+node.title}));expect(within(directory).queryByRole('button',{name:'范围概要',exact:true})).toBeNull();
});

it('keeps an inline title readable when an unknown save is reported globally',async()=>{
 f.api.command.mockRejectedValueOnce(new Error('应答丢失'));show();await ready();fireEvent.doubleClick(screen.getByRole('article',{name:node.title}));const input=screen.getByLabelText('节点标题');changeField(input,{target:{value:'短标题草稿'}});fireEvent.keyDown(input,{key:'Enter'});await screen.findAllByRole('alert');expect(fieldValue(input)).toBe('短标题草稿');expect(document.querySelector('.kc-inline-title [role="alert"]')).toBeNull();expect(screen.getByRole('alert')).toHaveTextContent('保存结果尚未确认');
});

describe('node image preview',()=>{
 it.each([false,true])('opens the shared viewer without image settings, readonly=%s',async readonly=>{
  f.api.scene.mockResolvedValue({...initial,containers:initial.containers.map(c=>({...c,role:readonly?'viewer':'manager'})),nodes:[{...node,canvas_data:{image:{binding_id:'picture',revision:3,width:200,aspect:2,placement:'bottom'}}}]});f.files.content.mockResolvedValue(new Blob(['image'],{type:'image/png'}));
  show();await ready();const button=screen.getByRole('button',{name:'放大图片 '+node.title});await waitFor(()=>expect(button).not.toBeDisabled());fireEvent.click(button);
  expect(await screen.findByAltText('预览图片')).toHaveAttribute('src',expect.stringContaining('blob:'));expect(screen.queryByRole('dialog',{name:'节点图片'})).not.toBeInTheDocument();
  fireEvent.click(screen.getByTitle('放大'));expect(screen.getByAltText('预览图片').style.transform).toContain('1.25');
  fireEvent.click(screen.getByTitle('关闭 (ESC)'));await waitFor(()=>expect(screen.queryByAltText('预览图片')).not.toBeInTheDocument());
  expect(f.files.content).toHaveBeenCalledWith('root','picture',3,expect.any(AbortSignal));expect(f.api.command).not.toHaveBeenCalled();
 });
});

it('shows bound attachment names and opens the exact version directly from the node',async()=>{
 const version={binding_id:'summary-image',revision:3,version_id:'immutable-3',asset_id:'asset',name:'绑定图片.png',mime_type:'image/png',size:256,sha256:'a'.repeat(64),text_status:'unsupported' as const,created_at:'2026-10-24'};
 f.api.scene.mockResolvedValue({...initial,nodes:[{...node,attachments:{total:3,items:[{id:'summary-image',title:'绑定图片.png',current_revision:3,version}]}}]});f.files.content.mockResolvedValue(new Blob(['image'],{type:'image/png'}));
 show();await ready();expect(screen.getByRole('button',{name:'共 3 个附件'})).toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'绑定图片.png'}));
 expect(await screen.findByAltText('预览图片')).toBeInTheDocument();expect(f.files.content).toHaveBeenCalledWith('root','summary-image',3,expect.any(AbortSignal));expect(f.api.object).not.toHaveBeenCalled();expect(f.api.command).not.toHaveBeenCalled();
});

it('opens direct add actions and uploads without opening the file drawer',async()=>{
 show();await ready();const button=screen.getByRole('button',{name:'文件资料',exact:true});fireEvent.keyDown(button,{key:'ArrowDown'});expect(screen.getByRole('menuitem',{name:'上传文件'})).toBeInTheDocument();expect(screen.getByRole('menu')).toHaveAttribute('data-side','bottom');expect(screen.getByRole('menu')).toHaveAttribute('data-align','start');expect(screen.getByRole('menuitem',{name:'从工作区选择'})).toBeInTheDocument();expect(screen.queryByRole('complementary',{name:'文件资料'})).not.toBeInTheDocument();expect(f.api.object).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('menuitem',{name:'上传文件'}));const file=new File(['body'],'直接上传.txt',{type:'text/plain'});fireEvent.change(screen.getByLabelText('上传文件到 '+node.title),{target:{files:[file]}});
 await waitFor(()=>expect(f.files.upload).toHaveBeenCalledWith('root',file,expect.any(String),{}));await waitFor(()=>expect(screen.queryByRole('dialog',{name:'添加文件'})).not.toBeInTheDocument());expect(screen.queryByRole('complementary',{name:'文件资料'})).not.toBeInTheDocument();
});
it('removes a displayed image without deleting its attachment or source',async()=>{
 f.api.scene.mockResolvedValue({...initial,nodes:[{...node,canvas_data:{image:{binding_id:'pic',revision:1,width:200,aspect:2,placement:'bottom'}}}]});f.files.content.mockResolvedValue(new Blob(['image'],{type:'image/png'}));show();await ready();fireEvent.click(screen.getByRole('button',{name:'移除节点图片 '+node.title}));await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_canvas_data',expect.objectContaining({updates:[expect.objectContaining({canvas_data:expect.objectContaining({image:null})})]}),expect.any(String)));expect(f.files.remove).not.toHaveBeenCalled();
});

it('does not let a late file drawer response replace a newer direct upload target',async()=>{
 const other={...node,id:'other',node_id:'other-place',title:'另一个节点',x:500};f.api.scene.mockResolvedValue({...initial,nodes:[node,other]});let finish!: (value:unknown)=>void;f.api.object.mockImplementation(id=>id==='root'?new Promise(resolve=>{finish=resolve;}):Promise.resolve({...other,body:'',can_write:true}));show();await ready();
 const rootButton=within(screen.getByRole('article',{name:node.title})).getByRole('button',{name:'文件资料'});fireEvent.keyDown(rootButton,{key:'ArrowDown'});fireEvent.click(screen.getByRole('menuitem',{name:'查看全部附件'}));
 const file=new File(['new'],'另一个节点.txt',{type:'text/plain'});fireEvent.change(screen.getByLabelText('上传文件到 另一个节点'),{target:{files:[file]}});await waitFor(()=>expect(f.files.upload).toHaveBeenCalledWith('other',file,expect.any(String),{}));
 await act(async()=>finish({...node,body:'',can_write:true}));expect(screen.queryByRole('complementary',{name:'文件资料'})).not.toBeInTheDocument();
});

 it('keeps labels in toolbars and edits existing notes from the node context menu',async()=>{
  f.api.scene.mockResolvedValue({...initial,nodes:[{...node,canvas_data:{note:'已有备注',labels:['平台'],task:{status:'todo'}}}]});show();await ready();
  fireEvent.pointerDown(screen.getByRole('article',{name:node.title}),{button:0,pointerId:1});fireEvent.pointerUp(screen.getByLabelText('知识画布'),{pointerId:1});
  expect(screen.queryByRole('button',{name:'备注',exact:true})).toBeNull();expect(screen.queryByRole('button',{name:'节点备注',exact:true})).toBeNull();expect(screen.queryByRole('button',{name:'标签',exact:true})).toBeNull();expect(screen.queryByRole('button',{name:'节点标签',exact:true})).toBeNull();
  openNote();expect(fieldValue(await screen.findByLabelText('备注内容'))).toBe('已有备注');changeField(screen.getByLabelText('备注内容'),{target:{value:'更新备注'}});fireEvent.click(screen.getByRole('button',{name:'保存备注'}));await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('update_canvas_data',expect.objectContaining({updates:[expect.objectContaining({object_id:'root',canvas_data:expect.objectContaining({note:'更新备注',labels:['平台']})})]}),expect.any(String)));
 });

 it.each([{platform:'MacIntel',copy:'⌘C',parent:'⌘Enter',single:'⌘⌫',branch:'⌫'},{platform:'Win32',copy:'Ctrl+C',parent:'Ctrl+Enter',single:'Ctrl+Delete',branch:'Delete'}])('organizes context actions and shows only $platform shortcuts',async({platform,copy,parent,single,branch})=>{
  vi.spyOn(window.navigator,'platform','get').mockReturnValue(platform);show();await ready();fireEvent.contextMenu(screen.getByRole('article',{name:node.title}),{clientX:200,clientY:200});
  const menu=screen.getByRole('menu',{name:'画布右键菜单'});expect(within(menu).getAllByRole('menuitem')).toHaveLength(9);
  for(const name of ['展开查看','修改标题','回到中心主题 · Home','关闭菜单','历史版本','创建文档'])expect(within(menu).queryByRole('menuitem',{name,exact:true})).toBeNull();
  expect(within(menu).getByRole('menuitem',{name:'复制整条分支 · '+copy})).toBeInTheDocument();
  const create=screen.getByRole('menuitem',{name:'创建',exact:true});fireEvent.keyDown(create,{key:'ArrowRight'});expect(await screen.findByRole('menuitem',{name:'插入父主题 · '+parent})).toBeInTheDocument();fireEvent.keyDown(screen.getByRole('menuitem',{name:'创建文档'}),{key:'Escape'});await waitFor(()=>expect(create).toHaveFocus());
  fireEvent.keyDown(screen.getByRole('menuitem',{name:'删除',exact:true}),{key:'ArrowRight'});expect(await screen.findByRole('menuitem',{name:'只删除当前主题（保留子主题） · '+single})).toBeInTheDocument();expect(screen.getByRole('menuitem',{name:'删除整条分支 · '+branch})).toBeInTheDocument();
  vi.restoreAllMocks();
 });

it('extends a large legacy image card horizontally with Tab and keeps that location on confirmation',async()=>{
 const large={...node,canvas_data:{width:500,height:600,image:{binding_id:'pic',revision:1,width:400,aspect:1}}};f.api.scene.mockResolvedValue({...initial,nodes:[large]});f.files.content.mockResolvedValue(new Blob(['image'],{type:'image/png'}));show();await ready();
 const article=screen.getByRole('article',{name:node.title});Object.defineProperty(article,'offsetWidth',{value:500,configurable:true});Object.defineProperty(article,'offsetHeight',{value:600,configurable:true});fireEvent.pointerDown(article,{button:0,pointerId:1});fireEvent.pointerUp(screen.getByLabelText('知识画布'),{pointerId:1});fireEvent.keyDown(screen.getByLabelText('知识画布'),{key:'Tab'});
 const input=screen.getByLabelText('新主题标题'),editor=input.closest('.kc-quick') as HTMLElement;expect(editor.style.left).toBe('600px');expect(editor.style.top).toBe('267px');changeField(input,{target:{value:'延伸子主题'}});fireEvent.keyDown(input,{key:'Enter'});await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('create_object',expect.objectContaining({parent_id:'root',position:{x:600,y:267}}),expect.any(String)));
});

it.each(['Enter','Tab'])('keeps the same right-side parent port before and after %s creation and refresh',async key=>{
 const large={...node,canvas_data:{width:500,height:600}},a={...node,id:'a',node_id:'pa',parent_id:'root',title:'末尾子主题',x:600,y:1600,sort_order:1};let current={...initial,nodes:[large,a]};f.api.scene.mockImplementation(async()=>current);
 f.api.command.mockImplementation(async(_operation,payload)=>{const p=payload as {parent_id:string;position:{x:number;y:number};title:string};current={...current,nodes:[...current.nodes,{...node,id:'new',node_id:'pn',parent_id:p.parent_id,title:p.title,x:p.position.x,y:p.position.y,sort_order:2}]};return {success:true,data:{object_id:'new'}};});
 show();await ready();const root=screen.getByRole('article',{name:node.title});Object.defineProperty(root,'offsetWidth',{value:500,configurable:true});Object.defineProperty(root,'offsetHeight',{value:600,configurable:true});const selected=key==='Tab'?root:screen.getByRole('article',{name:'末尾子主题'});fireEvent.pointerDown(selected,{button:0,pointerId:1});fireEvent.pointerUp(screen.getByLabelText('知识画布'),{pointerId:1});fireEvent.keyDown(screen.getByLabelText('知识画布'),{key});
 const draftPath=document.querySelector('.kc-draft-edge path')!;expect(draftPath.getAttribute('d')).toMatch(/^M 520 340 C /);const input=screen.getByLabelText('新主题标题');changeField(input,{target:{value:'新增主题'}});fireEvent.keyDown(input,{key:'Enter'});await screen.findByRole('article',{name:'新增主题'});expect(current.nodes.at(-1)?.parent_id).toBe('root');
 const finalPath=()=>document.querySelector('.kc-world > .kc-edges:not(.kc-draft-edge) path:last-child')??document.querySelector('.kc-edges:not(.kc-draft-edge) path:last-child');expect(finalPath()?.getAttribute('d')).toMatch(/^M 520 340 C /);fireEvent.click(screen.getByRole('button',{name:'刷新',exact:true}));await waitFor(()=>expect(finalPath()?.getAttribute('d')).toMatch(/^M 520 340 C /));
});


describe('collapsible directory actions',()=>{
 const child={...node,id:'dir-child',node_id:'dir-child',parent_id:'root',title:'目录子项',x:420,y:40};
 const grandchild={...child,id:'dir-grandchild',node_id:'dir-grandchild',parent_id:'dir-child',title:'目录孙项',x:820};
 it('limits default depth and toggles branches and project independently of the canvas',async()=>{
  f.api.scene.mockResolvedValue({...initial,nodes:[node,child,grandchild]});show();await ready();const dir=screen.getByLabelText('知识目录');
  expect(within(dir).getByRole('button',{name:child.title,exact:true})).toBeInTheDocument();expect(within(dir).queryByRole('button',{name:grandchild.title,exact:true})).toBeNull();
  fireEvent.click(within(dir).getByRole('button',{name:'展开目录 '+child.title}));expect(within(dir).getByRole('button',{name:grandchild.title,exact:true})).toBeInTheDocument();
  fireEvent.click(within(dir).getByRole('button',{name:'折叠目录 '+node.title}));expect(within(dir).queryByRole('button',{name:child.title,exact:true})).toBeNull();expect(screen.getByRole('article',{name:grandchild.title})).toBeInTheDocument();
  fireEvent.click(within(dir).getByRole('button',{name:'折叠项目目录 客户项目'}));expect(within(dir).queryByRole('button',{name:node.title,exact:true})).toBeNull();
  fireEvent.click(within(dir).getByRole('button',{name:'展开项目目录 客户项目'}));expect(within(dir).getByRole('button',{name:node.title,exact:true})).toBeInTheDocument();expect(f.api.command).not.toHaveBeenCalled();
 });
 it('keeps inactive project headers collapsed when switching projects',async()=>{
  const other={...node,id:'other',node_id:'other',container_id:'other-project',title:'另一项目主题'};
  f.api.scene.mockResolvedValue({...initial,containers:[...initial.containers,{id:'other-project',kind:'project',title:'另一项目',role:'manager',revision:1}],nodes:[node,other]});show();await ready();const dir=screen.getByLabelText('知识目录');
  fireEvent.click(within(dir).getByRole('button',{name:'展开项目目录 另一项目'}));
  expect(within(dir).getByRole('button',{name:'展开项目目录 客户项目'})).toHaveAttribute('aria-expanded','false');
  expect(within(dir).getByRole('button',{name:other.title,exact:true})).toBeInTheDocument();expect(within(dir).queryByRole('button',{name:node.title,exact:true})).toBeNull();
 });
 it('reveals collapsed ancestors in the directory when locating a search result',async()=>{
  f.api.scene.mockResolvedValue({...initial,nodes:[node,child,grandchild]});f.api.search.mockResolvedValue({items:[{...grandchild,excerpt:'命中'}],next_cursor:null});show();await ready();const dir=screen.getByLabelText('知识目录');
  fireEvent.click(within(dir).getByRole('button',{name:'折叠目录 '+node.title}));changeField(screen.getByLabelText('搜索当前范围标题与正文'),{target:{value:'目录孙项'}});
  fireEvent.click(await screen.findByRole('button',{name:'目录孙项 命中'}));await waitFor(()=>expect(screen.getByLabelText('知识画布')).toHaveFocus());
  changeField(screen.getByLabelText('搜索当前范围标题与正文'),{target:{value:''}});expect(await within(dir).findByRole('button',{name:grandchild.title,exact:true})).toBeInTheDocument();
 });
 it('deletes the clicked branch rather than an unrelated canvas selection and can undo',async()=>{
  f.api.scene.mockResolvedValue({...initial,nodes:[node,child,grandchild]});show();await ready();fireEvent.click(within(screen.getByLabelText('知识目录')).getByRole('button',{name:node.title,exact:true}));
  f.api.history.mockResolvedValue({undo:{history_id:'directory-delete',operation:'delete_branches',can_apply:true},redo:null});
  fireEvent.click(screen.getByRole('button',{name:'删除目录分支 '+child.title}));await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('delete_branches',expect.objectContaining({objects:[{object_id:child.id,expected_revision:1}]}),expect.any(String)));
  const undo=await screen.findByRole('button',{name:'撤销 删除分支'});await waitFor(()=>expect(undo).toBeEnabled());fireEvent.click(undo);await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('undo_canvas',{history_id:'directory-delete'},expect.any(String)));
 });
 it.each(['MacIntel','Win32'])('copies and pastes through the compact directory menu on %s',async platform=>{
  Object.defineProperty(window.navigator,'platform',{configurable:true,value:platform});f.api.scene.mockResolvedValue({...initial,nodes:[node,child]});show();await ready();const dir=screen.getByLabelText('知识目录');
  fireEvent.contextMenu(within(dir).getByRole('button',{name:child.title,exact:true}),{clientX:1050,clientY:400});
  expect(screen.getAllByRole('menuitem')).toHaveLength(3);expect(screen.getByRole('menuitem',{name:/粘贴分支/})).toHaveAttribute('data-disabled');
  const chord=platform==='MacIntel'?'⌘C':'Ctrl+C';fireEvent.click(screen.getByRole('menuitem',{name:'复制整条分支 · '+chord}));await snapshotSettled();
  expect(f.api.captureSnapshot).toHaveBeenCalledWith(expect.objectContaining({objects:[{object_id:child.id,expected_revision:1}]}));
  fireEvent.contextMenu(within(dir).getByRole('button',{name:node.title,exact:true}),{clientX:1050,clientY:300});fireEvent.click(screen.getByRole('menuitem',{name:/粘贴分支/}));
  await waitFor(()=>expect(f.api.command).toHaveBeenCalledWith('copy_branches',expect.objectContaining({parent_id:'root'}),expect.any(String)));
 });
 it('does not expose delete or enable mutations for a read-only branch',async()=>{
  f.api.scene.mockResolvedValue({...initial,containers:initial.containers.map(c=>c.id==='p'?{...c,role:'viewer'}:c),nodes:[node,child]});show();await ready();const dir=screen.getByLabelText('知识目录');
  expect(within(dir).queryByRole('button',{name:/删除目录分支/})).toBeNull();fireEvent.contextMenu(within(dir).getByRole('button',{name:child.title,exact:true}),{clientX:1000,clientY:300});
  for(const item of screen.getAllByRole('menuitem'))expect(item).toHaveAttribute('data-disabled');expect(f.api.command).not.toHaveBeenCalled();
 });
});
