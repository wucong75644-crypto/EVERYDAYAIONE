import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { KnowledgeSource } from './KnowledgeSource';
import { KnowledgeReferenceTags } from './KnowledgeReferenceTags';
import { knowledgeApi, knowledgeFilesApi } from '../../services/knowledge';
import { request } from '../../services/api';
import { useAuthStore } from '../../stores/useAuthStore';
vi.mock('../../services/knowledge',()=>({knowledgeApi:{version:vi.fn()},knowledgeFilesApi:{versions:vi.fn(),content:vi.fn()}}));
vi.mock('../../services/api',()=>({request:vi.fn()}));
vi.mock('../../preview/PreviewHost',()=>({default:({state}:{state:{kind:string;items?:{filename:string}[]}})=><div>{state.kind==='open' && `预览 ${state.items?.[0].filename}`}</div>}));
beforeEach(()=>{vi.clearAllMocks();useAuthStore.setState({user:null,currentOrgId:null});});
describe('knowledge answer sources',()=>{
  it('loads the exact historical document revision read-only',async()=>{
    vi.mocked(knowledgeApi.version).mockResolvedValue({object_id:'o1',revision:2,title:'旧规则',body:'七天内退货',created_at:'2026-10-23'});
    render(<KnowledgeSource objectId="o1" revision={2} bindingId={null}/>);
    expect(await screen.findByDisplayValue('七天内退货')).toHaveAttribute('readonly');
    expect(knowledgeApi.version).toHaveBeenCalledWith('o1',2,expect.any(AbortSignal));
  });
  it('shows revoked sources as unavailable, never substitutes the latest body',async()=>{
    vi.mocked(knowledgeApi.version).mockRejectedValue(new Error('denied'));
    render(<KnowledgeSource objectId="o1" revision={2} bindingId={null}/>);
    expect(await screen.findByRole('alert')).toHaveTextContent('来源已移除、无权访问或版本不可用');
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });
  it('locates an older file version through history pagination',async()=>{
    const file={binding_id:'b',revision:1,version_id:'v',asset_id:'a',name:'规则.txt',mime_type:'text/plain',size:9,sha256:'x',text_status:'ready' as const,created_at:'2026-10-23'};
    vi.mocked(knowledgeFilesApi.versions).mockResolvedValueOnce({items:[{...file,revision:52}],next_cursor:52}).mockResolvedValueOnce({items:[file],next_cursor:null});
    vi.mocked(knowledgeFilesApi.content).mockResolvedValue(new Blob(['original']));
    URL.createObjectURL=vi.fn(()=> 'blob:test');URL.revokeObjectURL=vi.fn();
    render(<KnowledgeSource objectId="o1" revision={1} bindingId="b"/>);
    expect(await screen.findByText('预览 规则.txt')).toBeInTheDocument();
    expect(knowledgeFilesApi.content).toHaveBeenCalledWith('o1','b',1,expect.any(AbortSignal));
    expect(knowledgeFilesApi.versions).toHaveBeenLastCalledWith('o1','b',52,expect.any(AbortSignal));
  });
  it('restores message root labels and clears them across identity changes',async()=>{
    vi.mocked(request).mockResolvedValueOnce({data:{title:'私人知识'}}).mockRejectedValueOnce(new Error('denied'));
    render(<KnowledgeReferenceTags references={[{root:{kind:'object',id:'r1'},policy:'latest_saved'}]}/>);
    expect(await screen.findByRole('link',{name:'◇ 私人知识'})).toHaveAttribute('href','/knowledge?object=r1');
    act(()=>useAuthStore.setState({currentOrgId:'other'}));
    await waitFor(()=>expect(screen.queryByText('◇ 私人知识')).not.toBeInTheDocument());
    expect(screen.getByText('知识引用不可用')).toBeInTheDocument();
  });
});


it('focuses the source close control and supports Escape without substituting the source version',async()=>{
  vi.mocked(knowledgeApi.version).mockResolvedValue({object_id:'o1',revision:2,title:'旧规则',body:'历史依据',created_at:'2026-10-23'});
  const close=vi.fn();render(<KnowledgeSource objectId="o1" revision={2} bindingId={null} onClose={close}/>);
  await screen.findByLabelText('回答来源正文');expect(screen.getByRole('button',{name:'关闭回答来源'})).toHaveFocus();
  fireEvent.keyDown(screen.getByLabelText('回答来源正文'),{key:'Escape'});expect(close).toHaveBeenCalledTimes(1);
});
