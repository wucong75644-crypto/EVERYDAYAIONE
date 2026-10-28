import {ApiRequestError} from '../../../services/api';
import {fireEvent,render,screen,waitFor,cleanup} from '@testing-library/react';
import {beforeEach,afterEach,describe,expect,it,vi} from 'vitest';
import {XmindImport} from '../XmindImport';
const api=vi.hoisted(()=>({importXmind:vi.fn(),progress:vi.fn()}));
vi.mock('../../../services/knowledge',()=>({knowledgeFilesApi:api}));
vi.mock('../../primitives/Dialog',()=>({Dialog:({children,open}:{children:React.ReactNode;open:boolean})=>open?<div role="dialog">{children}</div>:null}));
const result={status:'applied',container_id:'project',object_id:'root',folded_ids:['folded'],report:{topics:5,images:1,links:1,summaries:1,warnings:['外部链接未读取']}};
function show(identity='user:org',onImported=vi.fn().mockResolvedValue(undefined),target={id:'project',title:'当前团队',revision:4}){
  return {onImported,...render(<XmindImport identity={identity} target={target} disabled={false} onBusy={vi.fn()} onImported={onImported}/>)};
}
function choose(){
  fireEvent.click(screen.getByRole('button',{name:/^导入 Xmind 到 /}));
  const file=new File(['synthetic'],'map.xmind');
  Object.defineProperty(file,'arrayBuffer',{value:async()=>new TextEncoder().encode('synthetic').buffer});
  fireEvent.change(screen.getByLabelText('选择 Xmind 文件'),{target:{files:[file]}});
  fireEvent.click(screen.getByRole('button',{name:'导入到当前空间'}));
}
beforeEach(()=>{vi.resetAllMocks();sessionStorage.clear();api.importXmind.mockResolvedValue({data:result});api.progress.mockResolvedValue({framework:'complete',nodes:5,files:{ready:0,partial:0,pending:1,failed:0,unsupported:0},links_unread:1});
  vi.stubGlobal('crypto',{randomUUID:()=> '12345678-1234-1234-1234-123456789012',subtle:{digest:vi.fn().mockResolvedValue(new Uint8Array([1,2,3]).buffer)}});
});
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.restoreAllMocks();});
describe('Xmind import',()=>{
  it('shows counts and honest compatibility feedback after atomic success',async()=>{
    const {onImported}=show();choose();await screen.findByText(/共 5 个主题/);expect(onImported).toHaveBeenCalledWith(result);expect(api.importXmind).toHaveBeenCalledWith(expect.any(File),expect.any(String),'project',4);
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

it('keeps the original target revision on a committed retry after reload',async()=>{
 const first=show();choose();await screen.findByText(/共 5 个主题/);first.unmount();
 show('user:org',vi.fn().mockResolvedValue(undefined),{id:'project',title:'当前团队',revision:9});choose();await screen.findByText(/共 5 个主题/);
 expect(api.importXmind.mock.calls[1].slice(1)).toEqual([api.importXmind.mock.calls[0][1],'project',4]);
});

it('uses separate retry receipts for the same file in different spaces',async()=>{
 const first=show();choose();await screen.findByText(/共 5 个主题/);first.unmount();
 show('user:org',vi.fn().mockResolvedValue(undefined),{id:'personal',title:'个人知识库',revision:2});choose();await screen.findByText(/共 5 个主题/);
 expect(api.importXmind.mock.calls[1].slice(2)).toEqual(['personal',2]);expect(sessionStorage.length).toBe(2);
});

it('clears only a rejected version-conflict attempt so a refreshed target can retry',async()=>{
 api.importXmind.mockRejectedValueOnce(new ApiRequestError('VERSION_CONFLICT','请刷新后重试',409));
 const first=show();choose();await screen.findByRole('alert');expect(sessionStorage.length).toBe(0);first.unmount();
 show('user:org',vi.fn().mockResolvedValue(undefined),{id:'project',title:'当前团队',revision:9});choose();await screen.findByText(/共 5 个主题/);
 expect(api.importXmind.mock.calls[1].slice(2)).toEqual(['project',9]);
});

it('reports saved originals and stops pending-job polling when AI processing is off',async()=>{
 const timers=vi.spyOn(globalThis,'setTimeout');api.progress.mockResolvedValue({ai_processing_enabled:false,framework:'complete',nodes:5,files:{ready:0,pending:1},links_unread:1});show();choose();await screen.findByText(/原始附件已保存，AI 解析未启用/);expect(api.progress).toHaveBeenCalledOnce();expect(timers.mock.calls.some(([,ms])=>ms===3000)).toBe(false);expect(screen.queryByText(/待处理 1/)).toBeNull();
});
