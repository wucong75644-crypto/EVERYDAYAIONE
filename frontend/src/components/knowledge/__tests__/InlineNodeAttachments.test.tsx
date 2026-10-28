import {act,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {beforeEach,expect,it,vi} from 'vitest';
import {InlineNodeAttachments} from '../InlineNodeAttachments';
import type {KnowledgeNode,KnowledgeFile} from '../../../services/knowledge';
const api=vi.hoisted(()=>({list:vi.fn(),content:vi.fn()}));
vi.mock('../../../services/knowledge',()=>({knowledgeFilesApi:api}));
vi.mock('../../../preview/registry',()=>({resolveAdapter:()=>null}));
const file=(id:string,mime:string,name:string,revision=1):KnowledgeFile=>({id,title:name,current_revision:revision,version:{binding_id:id,revision,version_id:id+revision,asset_id:id,name,mime_type:mime,size:20,sha256:'a',text_status:'unsupported',created_at:''}});
const node=(files:KnowledgeFile[]):KnowledgeNode=>({id:'node',node_id:'node',container_id:'team',parent_id:null,title:'节点',kind:'topic',revision:1,layout_revision:1,x:0,y:0,attachments:{total:files.length,items:files}});
beforeEach(()=>{vi.resetAllMocks();vi.stubGlobal('IntersectionObserver',undefined);URL.createObjectURL=vi.fn(()=> 'blob:protected');URL.revokeObjectURL=vi.fn();api.content.mockResolvedValue(new Blob(['content']));});
it('shows uploaded images and native video players directly, without a modal or external URL',async()=>{
 const onPreview=vi.fn();render(<InlineNodeAttachments node={node([file('pic','image/png','截图.png'),file('vid','video/mp4','培训.mp4',2)])} onSize={vi.fn()} onPreview={onPreview}/>);
 expect(await screen.findByRole('img',{name:'截图.png'})).toHaveAttribute('src','blob:protected');const video=screen.getByLabelText('培训.mp4');expect(video.tagName).toBe('VIDEO');expect(video).toHaveAttribute('controls');expect(video).toHaveAttribute('src','blob:protected');expect(screen.queryByRole('dialog')).toBeNull();expect(onPreview).not.toHaveBeenCalled();expect(api.content).toHaveBeenCalledWith('node','vid',2,expect.any(AbortSignal));fireEvent.click(screen.getByRole('button',{name:'放大预览 培训.mp4'}));expect(onPreview).toHaveBeenCalledWith(expect.objectContaining({binding_id:'vid',revision:2}));
});
it('does not duplicate a bound canvas image or put the Xmind source archive in the topic content',()=>{
 const n=node([file('pic','image/png','截图.png'),file('source','application/zip','原件.xmind')]);n.canvas_data={image:{binding_id:'pic',revision:1,width:220,placement:'top'}};render(<InlineNodeAttachments node={n} onSize={vi.fn()} onPreview={vi.fn()}/>);expect(api.content).not.toHaveBeenCalled();expect(screen.queryByText('原件.xmind')).toBeNull();
});
it('loads all attachments when the scene supplied only a short summary',async()=>{
 const files=[file('a','image/png','一.png'),file('b','image/png','二.png'),file('c','image/png','三.png')];api.list.mockResolvedValue(files);const n=node(files);n.attachments!.items=files.slice(0,2);render(<InlineNodeAttachments node={n} onSize={vi.fn()} onPreview={vi.fn()}/>);expect(await screen.findByRole('img',{name:'三.png'})).toBeVisible();expect(api.list).toHaveBeenCalledWith('node',expect.any(AbortSignal));
});
it('does not fetch offscreen files before they become visible',async()=>{
 let enter!:IntersectionObserverCallback;vi.stubGlobal('IntersectionObserver',class{constructor(cb:IntersectionObserverCallback){enter=cb;}observe(){}disconnect(){}});render(<InlineNodeAttachments node={node([file('a','image/png','一.png')])} onSize={vi.fn()} onPreview={vi.fn()}/>);expect(api.content).not.toHaveBeenCalled();expect(screen.getByText('附件待预览')).toBeVisible();act(()=>enter([{isIntersecting:true} as IntersectionObserverEntry],{} as IntersectionObserver));expect(await screen.findByRole('img',{name:'一.png'})).toBeVisible();
});
it('ignores stale bytes after the immutable file version changes and releases its blob URL',async()=>{
 let finish!:(blob:Blob)=>void;api.content.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));const view=render(<InlineNodeAttachments node={node([file('a','image/png','一.png')])} onSize={vi.fn()} onPreview={vi.fn()}/>);view.rerender(<InlineNodeAttachments node={node([file('a','image/png','一.png',2)])} onSize={vi.fn()} onPreview={vi.fn()}/>);await screen.findByRole('img',{name:'一.png'});await act(async()=>finish(new Blob(['old'])));expect(URL.createObjectURL).toHaveBeenCalledTimes(1);view.unmount();expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:protected');
});
it('keeps an explicit error and retries the same protected version when a read fails',async()=>{
 api.content.mockRejectedValueOnce(new Error('没有访问权限'));render(<InlineNodeAttachments node={node([file('a','image/png','一.png',2)])} onSize={vi.fn()} onPreview={vi.fn()}/>);expect(await screen.findByRole('alert')).toHaveTextContent('没有访问权限');fireEvent.click(screen.getByRole('button',{name:'重试读取'}));await waitFor(()=>expect(api.content).toHaveBeenCalledTimes(2));expect(api.content.mock.calls[0].slice(0,3)).toEqual(api.content.mock.calls[1].slice(0,3));
});
