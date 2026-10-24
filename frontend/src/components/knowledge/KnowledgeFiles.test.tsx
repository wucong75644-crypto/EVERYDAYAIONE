import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { KnowledgeFiles } from './KnowledgeFiles';
const workspace=vi.hoisted(()=>({list:vi.fn()}));
vi.mock('../../services/workspace',()=>({listWorkspace:workspace.list}));
const api = vi.hoisted(() => ({ list:vi.fn(),versions:vi.fn(),upload:vi.fn(),snapshot:vi.fn(),reference:vi.fn(),remove:vi.fn(),content:vi.fn() }));
vi.mock('../../services/knowledge', () => ({ knowledgeFilesApi:api }));
vi.mock('../../preview/registry', () => ({ resolveAdapter:() => ({id:'text'}) }));
vi.mock('../../preview/PreviewHost', () => ({ default:({state,onClose}: {state:{kind:string;items?:{filename:string}[]};onClose:()=>void}) => state.kind==='open' ? <div role="dialog" aria-label="文件预览"><span>{state.items?.[0]?.filename}</span><button onClick={onClose}>关闭预览</button></div> : null }));
const object = { id:'target',title:'节点资料',can_write:true,body:'',container_id:'p',parent_id:null,kind:'topic' as const,revision:1 };
const version = { binding_id:'binding',revision:2,version_id:'immutable-v2',asset_id:'asset',name:'资料.txt',mime_type:'text/plain',size:15,sha256:'a'.repeat(64),text_status:'ready' as const,created_at:'2026-10-22' };
const file = { id:'binding',title:'资料.txt',current_revision:2,version };
const source = { id:'source',title:'来源节点',container_id:'p',parent_id:null,kind:'document' as const,revision:1,node_id:'node',x:0,y:0,layout_revision:1 };
beforeEach(() => {
  vi.resetAllMocks();workspace.list.mockResolvedValue({path:'.',items:[{name:'来源.txt',is_dir:false,size:12,mime_type:'text/plain'}],total:1}); api.list.mockResolvedValue([file]); api.versions.mockResolvedValue({items:[version,{...version,revision:1,version_id:'immutable-v1'}],next_cursor:null});
  api.upload.mockResolvedValue({});api.reference.mockResolvedValue({});api.remove.mockResolvedValue({});api.snapshot.mockResolvedValue({});api.content.mockResolvedValue(new Blob(['file']));
  URL.createObjectURL=vi.fn(()=>'blob:private-file');URL.revokeObjectURL=vi.fn();
});
function show(canWrite=true) { return render(<KnowledgeFiles object={{...object,can_write:canWrite}} nodes={[source]} onClose={vi.fn()} onBusyChange={vi.fn()} />); }
async function menu(name:string){fireEvent.keyDown(screen.getByRole('button',{name}),{key:'ArrowDown'});}
async function ready() { await screen.findByRole('heading',{name:'资料.txt'}); }
describe('knowledge private files', () => {
  it('uploads a file and reuses the same command after an uncertain response', async () => {
    api.upload.mockRejectedValueOnce(new Error('network lost'));
    show();await ready();const selected=new File(['body'],'new.txt',{type:'text/plain'});
    fireEvent.change(screen.getByLabelText('上传并绑定文件'),{target:{files:[selected]}});
    await screen.findByRole('button',{name:'重试同一操作'});
    const first=api.upload.mock.calls[0];
    expect(screen.getByLabelText('上传并绑定文件')).toBeDisabled();
    expect(screen.getByRole('button',{name:'关闭文件资料'})).toBeDisabled();
    fireEvent.click(screen.getByRole('button',{name:'重试同一操作'}));
    await waitFor(()=>expect(api.upload).toHaveBeenCalledTimes(2));
    expect(api.upload.mock.calls[1]).toEqual(first);
    await waitFor(()=>expect(screen.queryByRole('button',{name:'重试同一操作'})).not.toBeInTheDocument());
  });
  it('does not overwrite a successful upload with the late initial listing', async () => {
    let resolve!: (rows: unknown[]) => void;
    api.list.mockReturnValueOnce(new Promise(r => { resolve = r; }));
    show();
    fireEvent.change(screen.getByLabelText('上传并绑定文件'),{target:{files:[new File(['body'],'new.txt')]}});
    await ready();
    expect(api.list.mock.calls[0][1].aborted).toBe(true);
    resolve([]); await Promise.resolve();
    expect(screen.getByRole('heading',{name:'资料.txt'})).toBeInTheDocument();
  });
  it('uploads a new version with the expected revision and keeps a conflict visible', async () => {
    api.upload.mockRejectedValue(Object.assign(new Error('版本冲突'),{status:409}));
    show();await ready();fireEvent.change(screen.getByLabelText('上传新版 资料.txt'),{target:{files:[new File(['v3'],'新版.txt')]}});
    await screen.findByRole('alert');
    expect(api.upload).toHaveBeenCalledWith('target',expect.any(File),expect.any(String),{binding_id:'binding',expected_revision:2});
    expect(screen.queryByRole('button',{name:'重试同一操作'})).not.toBeInTheDocument();
  });
  it('reads exactly an older immutable version and releases its private blob on close', async () => {
    show();await ready();await menu('更多操作 资料.txt');fireEvent.click(await screen.findByRole('menuitem',{name:'历史版本'}));fireEvent.click(screen.getByRole('button',{name:'查看文件版本'}));
    await screen.findByRole('option',{name:/版本 1/});
    fireEvent.change(screen.getByLabelText('文件版本 资料.txt'),{target:{value:'1'}});
    fireEvent.click(screen.getByRole('button',{name:'预览此版本'}));
    await screen.findByRole('dialog',{name:'文件预览'});
    expect(api.content).toHaveBeenCalledWith('target','binding',1,expect.any(AbortSignal));
    fireEvent.click(screen.getByRole('button',{name:'关闭预览'}));
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:private-file');
  });
  it('references the explicitly selected old version rather than following the source head', async () => {
    show();await ready();await menu('添加文件');fireEvent.click(await screen.findByRole('menuitem',{name:'引用其他节点文件'}));fireEvent.change(screen.getByLabelText('来源节点'),{target:{value:'source'}});
    await screen.findByRole('option',{name:'资料.txt'});
    fireEvent.change(screen.getByLabelText('来源文件'),{target:{value:'binding'}});
    await screen.findByRole('option',{name:/版本 1/});
    fireEvent.change(screen.getByLabelText('引用固定版本'),{target:{value:'1'}});
    fireEvent.click(screen.getByRole('button',{name:'添加固定版本引用'}));
    await waitFor(()=>expect(api.reference).toHaveBeenCalledWith('target','source','binding',1,expect.any(String)));
  });
  it('creates a snapshot from a relative Workspace path', async () => {
    show();await ready();await menu('添加文件');fireEvent.click(await screen.findByRole('menuitem',{name:'从工作区选择'}));fireEvent.click(await screen.findByRole('button',{name:'来源.txt'}));
    await waitFor(()=>expect(api.snapshot).toHaveBeenCalledWith('target','来源.txt',expect.any(String)));
  });
  it('allows viewers to preview, but offers no mutation controls', async () => {
    show(false);await ready();expect(screen.getByRole('button',{name:'预览文件 资料.txt'})).toBeEnabled();
    expect(screen.queryByLabelText('上传并绑定文件')).not.toBeInTheDocument();
    expect(screen.queryByRole('button',{name:'移除此附件'})).not.toBeInTheDocument();
    expect(screen.queryByLabelText('来源节点')).not.toBeInTheDocument();
  });
  it('does not open a stale preview after the panel is removed', async () => {
    let resolve!: (blob:Blob)=>void;api.content.mockReturnValue(new Promise(r=>{resolve=r;}));
    const view=show();await ready();fireEvent.click(screen.getByRole('button',{name:'预览文件 资料.txt'}));
    await waitFor(()=>expect(api.content).toHaveBeenCalled());const signal=api.content.mock.calls[0][3];
    view.unmount();expect(signal.aborted).toBe(true);resolve(new Blob(['private']));
    await Promise.resolve();expect(URL.createObjectURL).not.toHaveBeenCalled();
  });
});

it('focuses the file panel close control and supports Escape when idle',async()=>{
  const close=vi.fn();render(<KnowledgeFiles object={object} nodes={[source]} onClose={close} onBusyChange={vi.fn()}/>);await ready();
  expect(screen.getByRole('button',{name:'关闭文件资料'})).toHaveFocus();
  fireEvent.keyDown(screen.getByRole('button',{name:'关闭文件资料'}),{key:'Escape'});expect(close).toHaveBeenCalledTimes(1);
});
it('keeps the file panel open while an upload result is unknown or its preview is active',async()=>{
  const close=vi.fn();api.upload.mockRejectedValueOnce(new Error('unknown result'));
  render(<KnowledgeFiles object={object} nodes={[source]} onClose={close} onBusyChange={vi.fn()}/>);await ready();
  fireEvent.click(screen.getByRole('button',{name:'预览文件 资料.txt'}));const preview=await screen.findByRole('dialog',{name:'文件预览'});
  fireEvent.keyDown(preview,{key:'Escape'});expect(close).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'关闭预览'}));
  fireEvent.change(screen.getByLabelText('上传并绑定文件'),{target:{files:[new File(['body'],'new.txt')]}});
  await screen.findByRole('button',{name:'重试同一操作'});
  fireEvent.keyDown(screen.getByLabelText('上传并绑定文件'),{key:'Escape'});expect(close).not.toHaveBeenCalled();
});

it('keeps the default list simple and previews the bound head from its row',async()=>{
 show();await ready();expect(screen.queryByLabelText('Workspace 相对路径')).not.toBeInTheDocument();expect(screen.queryByRole('button',{name:'查看文件版本'})).not.toBeInTheDocument();expect(screen.queryByLabelText('来源节点')).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'预览文件 资料.txt'}));await screen.findByRole('dialog',{name:'文件预览'});expect(api.content).toHaveBeenCalledWith('target','binding',2,expect.any(AbortSignal));expect(api.upload).not.toHaveBeenCalled();expect(api.reference).not.toHaveBeenCalled();
});
it('confirms removal and refreshes the attachment summary without deleting a source',async()=>{
 const changed=vi.fn();api.list.mockResolvedValueOnce([file]).mockResolvedValue([]);render(<KnowledgeFiles object={object} nodes={[source]} onClose={vi.fn()} onBusyChange={vi.fn()} onFilesChanged={changed}/>);await ready();await menu('更多操作 资料.txt');fireEvent.click(await screen.findByRole('menuitem',{name:'移除附件'}));expect(api.remove).not.toHaveBeenCalled();expect(screen.getByText(/源文件和其他节点的引用不会删除/)).toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'移除附件'}));await waitFor(()=>expect(changed).toHaveBeenCalledWith([]));expect(api.remove).toHaveBeenCalledWith('target',file,expect.any(String));expect(api.upload).not.toHaveBeenCalled();
});
it('executes a direct upload once and closes without leaving the canvas busy',async()=>{const closed=vi.fn(),busy=vi.fn(),entry={kind:'upload' as const,file:new File(['file'],'直接.txt')};render(<KnowledgeFiles object={object} nodes={[source]} entry={entry} position={{left:50,top:200}} onClose={closed} onBusyChange={busy}/>);await waitFor(()=>expect(api.upload).toHaveBeenCalledExactlyOnceWith('target',entry.file,expect.any(String),{}));await waitFor(()=>expect(closed).toHaveBeenCalledOnce());expect(busy).toHaveBeenLastCalledWith(false);expect(screen.queryByRole('complementary',{name:'文件资料'})).not.toBeInTheDocument();});
