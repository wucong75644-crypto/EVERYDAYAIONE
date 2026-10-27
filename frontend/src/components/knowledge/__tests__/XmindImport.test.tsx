import {fireEvent,render,screen,waitFor,cleanup} from '@testing-library/react';
import {beforeEach,afterEach,describe,expect,it,vi} from 'vitest';
import {XmindImport} from '../XmindImport';
const api=vi.hoisted(()=>({importXmind:vi.fn(),progress:vi.fn()}));
vi.mock('../../../services/knowledge',()=>({knowledgeFilesApi:api}));
vi.mock('../../primitives/Dialog',()=>({Dialog:({children,open}:{children:React.ReactNode;open:boolean})=>open?<div role="dialog">{children}</div>:null}));
const result={status:'applied',container_id:'project',object_id:'root',folded_ids:['folded'],report:{topics:5,images:1,links:1,summaries:1,warnings:['外部链接未读取']}};
function show(identity='user:org',onImported=vi.fn().mockResolvedValue(undefined)){
  return {onImported,...render(<XmindImport identity={identity} disabled={false} onBusy={vi.fn()} onImported={onImported}/>)};
}
function choose(){
  fireEvent.click(screen.getByRole('button',{name:'导入 Xmind'}));
  const file=new File(['synthetic'],'map.xmind');
  Object.defineProperty(file,'arrayBuffer',{value:async()=>new TextEncoder().encode('synthetic').buffer});
  fireEvent.change(screen.getByLabelText('选择 Xmind 文件'),{target:{files:[file]}});
  fireEvent.click(screen.getByRole('button',{name:'导入为新项目'}));
}
beforeEach(()=>{vi.resetAllMocks();sessionStorage.clear();api.importXmind.mockResolvedValue({data:result});api.progress.mockResolvedValue({framework:'complete',nodes:5,files:{ready:0,partial:0,pending:1,failed:0,unsupported:0},links_unread:1});
  vi.stubGlobal('crypto',{randomUUID:()=> '12345678-1234-1234-1234-123456789012',subtle:{digest:vi.fn().mockResolvedValue(new Uint8Array([1,2,3]).buffer)}});
});
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.restoreAllMocks();});
describe('Xmind import',()=>{
  it('shows counts and honest compatibility feedback after atomic success',async()=>{
    const {onImported}=show();choose();await screen.findByText(/共 5 个主题/);expect(onImported).toHaveBeenCalledWith(result);
    expect(screen.getByText('外部链接未读取')).toBeInTheDocument();
    expect(screen.getByRole('button',{name:'完成'})).toBeEnabled();
  });
  it('persists the command before sending and reuses it after a lost response and reload',async()=>{
    api.importXmind.mockRejectedValueOnce(new Error('response lost'));
    const first=show();choose();await screen.findByRole('alert');const command=api.importXmind.mock.calls[0][1];
    fireEvent.click(screen.getByRole('button',{name:'重试并核对导入'}));await screen.findByText(/共 5 个主题/);
    expect(api.importXmind.mock.calls[1][1]).toBe(command);
    first.unmount();show();choose();await screen.findByText(/共 5 个主题/);
    expect(api.importXmind.mock.calls[2][1]).toBe(command);
  });
  it('does not navigate or expose a late response after switching identity',async()=>{
    let finish!:(value:unknown)=>void;api.importXmind.mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
    const first=show();choose();await waitFor(()=>expect(api.importXmind).toHaveBeenCalled());first.unmount();show('other:org');
    finish({data:result});await Promise.resolve();expect(first.onImported).not.toHaveBeenCalled();expect(screen.queryByText(/共 5 个主题/)).not.toBeInTheDocument();
  });
  it('keeps a successful result if scene refresh fails and reports a hidden replay accurately',async()=>{
    api.importXmind.mockResolvedValue({data:{...result,status:'replayed',hidden:true}});
    show('user:org',vi.fn().mockRejectedValue(new Error('refresh failed')));choose();
    await screen.findByRole('alert');expect(screen.getByText(/这次导入已撤销/)).toBeInTheDocument();
    expect(screen.getByRole('button',{name:'完成'})).toBeEnabled();
  });
  it('fails before sending when it cannot persist the retry receipt',async()=>{
    vi.spyOn(Storage.prototype,'setItem').mockImplementationOnce(()=>{throw new Error('storage disabled');});
    show();choose();await screen.findByRole('alert');expect(api.importXmind).not.toHaveBeenCalled();
  });
});

it('separates completed nodes from pending and failed attachment parsing',async()=>{
 api.progress.mockResolvedValue({framework:'complete',nodes:5,files:{ready:1,partial:2,pending:3,failed:4,unsupported:0},links_unread:1});
 show();choose();
 expect(await screen.findByText(/附件：已解析 1，部分解析 2，待处理 3，失败 4/)).toBeInTheDocument();
 expect(screen.getByText(/框架与节点已建立，可以立即查看/)).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'完成'})).toBeEnabled();
});
