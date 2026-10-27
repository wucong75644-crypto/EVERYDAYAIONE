import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {ApiRequestError} from '../../../services/api';
import {DerivedDirectory,DerivedTags} from '../DerivedDirectory';
const f=vi.hoisted(()=>({document:vi.fn(),override:vi.fn(),auth:{user:{id:'u'},currentOrgId:'o'}}));
vi.mock('../../../services/knowledge',()=>({knowledgeApi:{document:f.document,override:f.override}}));
vi.mock('../../../stores/useAuthStore',()=>({useAuthStore:(select:(v:typeof f.auth)=>unknown)=>select(f.auth)}));
const derived={generation_id:'g',status:'ready',sections:[{id:'section:s',title:'处理步骤',text:'核对数量后补寄',source_refs:['file:f'],kind:'material'}],tags:['人工平台','补寄'],auto_tags:['补寄'],source_links:{'file:f':'/knowledge?object=n&binding=f&revision=1'},override_revisions:{}};
beforeEach(()=>{vi.resetAllMocks();localStorage.clear();f.auth.currentOrgId='o';f.document.mockResolvedValue({id:'n',body:'人工原文',derived});f.override.mockResolvedValue({data:{revision:1}});});
afterEach(()=>{cleanup();vi.useRealTimers();});
it('shows source-backed auto content without changing manual body',async()=>{render(<DerivedDirectory objectId="n" writable/>);expect(await screen.findByText('核对数量后补寄')).toBeInTheDocument();expect(screen.getByRole('link',{name:'查看来源'})).toHaveAttribute('href','/knowledge?object=n&binding=f&revision=1');expect(f.override).not.toHaveBeenCalled();});
it('retains the exact manual correction request after an uncertain result',async()=>{f.override.mockRejectedValueOnce(new Error('网络中断'));render(<DerivedDirectory objectId="n" writable/>);fireEvent.click(await screen.findByRole('button',{name:'修改此段'}));fireEvent.change(screen.getByLabelText('人工更正'),{target:{value:'人工修正步骤'}});fireEvent.click(screen.getByRole('button',{name:'保存更正'}));await screen.findByRole('alert');const first=f.override.mock.calls[0];fireEvent.click(screen.getByRole('button',{name:'重试核对'}));await waitFor(()=>expect(f.override).toHaveBeenCalledTimes(2));expect(f.override.mock.calls[1]).toEqual(first);expect(first.slice(0,4)).toEqual(['n','section:s',{text:'人工修正步骤'},0]);});
it('restores a correction draft after reopening but not for another organization',async()=>{const first=render(<DerivedDirectory objectId="n" writable/>);fireEvent.click(await screen.findByRole('button',{name:'修改此段'}));fireEvent.change(screen.getByLabelText('人工更正'),{target:{value:'尚未保存的人工修正'}});first.unmount();const second=render(<DerivedDirectory objectId="n" writable/>);expect(await screen.findByLabelText('人工更正')).toHaveValue('尚未保存的人工修正');second.unmount();f.auth.currentOrgId='other';render(<DerivedDirectory objectId="n" writable/>);await screen.findByText('核对数量后补寄');expect(screen.queryByLabelText('人工更正')).toBeNull();});
it('never exposes correction drafts through read-only rendering',async()=>{localStorage.setItem('kc-section:u:o:n:section:s',JSON.stringify({text:'私有未保存草稿',savedAt:Date.now()}));render(<DerivedDirectory objectId="n" writable={false}/>);await screen.findByText('核对数量后补寄');expect(screen.queryByText('私有未保存草稿')).toBeNull();expect(screen.queryByRole('button',{name:'修改此段'})).toBeNull();});
it('hides only auto tags and keeps manual tag editing independent',async()=>{render(<DerivedTags objectId="n" writable/>);fireEvent.click(await screen.findByRole('button',{name:'隐藏自动标签 补寄'}));await waitFor(()=>expect(f.override).toHaveBeenCalledWith('n','tag:补寄',{hidden:true},0,expect.any(String)));expect(screen.queryByRole('button',{name:'隐藏自动标签 人工平台'})).toBeNull();});
it('removes stale content when the next authorization check fails',async()=>{vi.useFakeTimers();render(<DerivedDirectory objectId="n" writable/>);await act(async()=>{await Promise.resolve();});expect(screen.getByText('核对数量后补寄')).toBeInTheDocument();f.document.mockRejectedValueOnce(new Error('内容不存在或无权访问'));await act(async()=>{await vi.advanceTimersByTimeAsync(4000);});expect(screen.queryByText('核对数量后补寄')).toBeNull();expect(screen.getByRole('alert')).toHaveTextContent('无权访问');});

it('preserves correction text after a definite version conflict',async()=>{
 f.override.mockRejectedValueOnce(new ApiRequestError('VERSION_CONFLICT','版本冲突',409));
 render(<DerivedDirectory objectId="n" writable/>);
 fireEvent.click(await screen.findByRole('button',{name:'修改此段'}));
 fireEvent.change(screen.getByLabelText('人工更正'),{target:{value:'不能丢失的更正'}});
 fireEvent.click(screen.getByRole('button',{name:'保存更正'}));
 await screen.findByText('资料版本已变化，已保留你的更正。请核对最新资料后再次保存。');
 expect(screen.getByLabelText('人工更正')).toHaveValue('不能丢失的更正');
 expect(screen.getByLabelText('人工更正')).not.toHaveAttribute('readonly');
 expect(JSON.parse(localStorage.getItem('kc-section:u:o:n:section:s')!).text).toBe('不能丢失的更正');
});


it('distinguishes legacy summaries from verified output',async()=>{
 const first=render(<DerivedDirectory objectId="legacy" writable={false}/>);
 expect(await screen.findByText('此处包含尚未核验的旧整理，请对照原文查看。')).toBeInTheDocument();
 first.unmount();f.document.mockResolvedValue({derived:{...derived,sections:derived.sections.map(s=>({...s,verification:'verified'}))}});
 render(<DerivedDirectory objectId="verified" writable={false}/>);
 await screen.findByText('核对数量后补寄');
 expect(screen.queryByText('此处包含尚未核验的旧整理，请对照原文查看。')).toBeNull();
});

it('labels the original-excerpt fallback instead of claiming summary verification',async()=>{
 f.document.mockResolvedValue({derived:{...derived,sections:derived.sections.map(s=>({...s,verification:'extractive'}))}});
 render(<DerivedDirectory objectId="excerpt" writable={false}/>);
 expect(await screen.findByText('部分内容为原文摘录，自动整理未通过核验。')).toBeInTheDocument();
});

it('shows branch navigation when overview verification fails without claiming original excerpts',async()=>{
 f.document.mockResolvedValue({derived:{...derived,sections:[{...derived.sections[0],text:'采购分支',verification:'navigation'}]}});
 render(<DerivedDirectory objectId="navigation" writable={false}/>);
 expect(await screen.findByText('自动概览未通过核验，以下保留分支入口，请进入分支查看原文。')).toBeInTheDocument();
 expect(screen.queryByText('部分内容为原文摘录，自动整理未通过核验。')).toBeNull();
 expect(await screen.findByText('采购分支')).toBeInTheDocument();
});

it('labels local parsed originals without presenting them as AI summaries',async()=>{
 f.document.mockResolvedValue({derived:{...derived,sections:derived.sections.map(s=>({...s,verification:'source'}))}});
 render(<DerivedDirectory objectId="local" writable={false}/>);
 expect(await screen.findByRole('region',{name:'节点解析资料'})).toBeInTheDocument();
 expect(screen.getByText('以下为本节点原文及附件解析文字，未生成 AI 摘要。')).toBeInTheDocument();
 expect(screen.queryByText('此处包含尚未核验的旧整理，请对照原文查看。')).toBeNull();
});
