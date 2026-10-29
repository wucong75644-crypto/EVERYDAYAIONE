import {act,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {beforeEach,expect,it,vi} from 'vitest';
import {InlineNodeAttachments} from '../InlineNodeAttachments';
import type {KnowledgeNode,KnowledgeFile} from '../../../services/knowledge';
const api=vi.hoisted(()=>({list:vi.fn(),content:vi.fn(),thumbnail:vi.fn(),playback:vi.fn()}));
vi.mock('../../../services/knowledge',()=>({knowledgeFilesApi:api}));
vi.mock('../../../preview/registry',()=>({resolveAdapter:()=>null}));
const file=(id:string,mime:string,name:string,revision=1):KnowledgeFile=>({id,title:name,current_revision:revision,version:{binding_id:id,revision,version_id:id+revision,asset_id:id,name,mime_type:mime,size:20,sha256:'a',text_status:'unsupported',created_at:''}});
const node=(files:KnowledgeFile[]):KnowledgeNode=>({id:'node',node_id:'node',container_id:'team',parent_id:null,title:'节点',kind:'topic',revision:1,layout_revision:1,x:0,y:0,attachments:{total:files.length,items:files}});
beforeEach(()=>{vi.resetAllMocks();vi.stubGlobal('IntersectionObserver',undefined);URL.createObjectURL=vi.fn(()=> 'blob:protected');URL.revokeObjectURL=vi.fn();api.content.mockResolvedValue(new Blob(['content']));api.thumbnail.mockResolvedValue(new Blob(['thumb']));api.playback.mockResolvedValue({url:'/api/private/stream',expires_in:300});vi.spyOn(HTMLMediaElement.prototype,'pause').mockImplementation(()=>{});vi.spyOn(HTMLMediaElement.prototype,'load').mockImplementation(()=>{});});
it('loads thumbnails but no video bytes until an explicit play click',async()=>{
 const onPreview=vi.fn();const view=render(<InlineNodeAttachments node={node([file('pic','image/png','截图.png'),file('vid','video/mp4','培训.mp4',2)])} onSize={vi.fn()} onPreview={onPreview}/>);
 expect(await screen.findByRole('img',{name:'截图.png'})).toHaveAttribute('src','blob:protected');expect(api.content).not.toHaveBeenCalled();expect(api.playback).not.toHaveBeenCalled();fireEvent.click(screen.getByRole('button',{name:'播放视频 培训.mp4'}));expect(await screen.findByLabelText('培训.mp4')).toHaveAttribute('src','/api/private/stream');expect(api.playback).toHaveBeenCalledWith('node','vid',2,expect.any(AbortSignal),'preview');view.unmount();
});
it('does not duplicate a bound canvas image or put the Xmind source archive in the topic content',()=>{
 const n=node([file('pic','image/png','截图.png'),file('source','application/zip','原件.xmind')]);n.canvas_data={image:{binding_id:'pic',revision:1,width:220,placement:'top'}};render(<InlineNodeAttachments node={n} onSize={vi.fn()} onPreview={vi.fn()}/>);expect(api.content).not.toHaveBeenCalled();expect(screen.queryByText('原件.xmind')).toBeNull();
});
it('loads all attachments when the scene supplied only a short summary',async()=>{
 const files=[file('a','image/png','一.png'),file('b','image/png','二.png'),file('c','image/png','三.png')];api.list.mockResolvedValue(files);const n=node(files);n.attachments!.items=files.slice(0,2);render(<InlineNodeAttachments node={n} onSize={vi.fn()} onPreview={vi.fn()}/>);expect(await screen.findByRole('img',{name:'三.png'})).toBeVisible();expect(api.list).toHaveBeenCalledWith('node',expect.any(AbortSignal));
});
it('does not fetch offscreen files before they become visible',async()=>{
 const observers:IntersectionObserverCallback[]=[];vi.stubGlobal('IntersectionObserver',class{constructor(cb:IntersectionObserverCallback){observers.push(cb);}observe(){}disconnect(){}});render(<InlineNodeAttachments node={node([file('a','image/png','一.png')])} onSize={vi.fn()} onPreview={vi.fn()}/>);expect(api.content).not.toHaveBeenCalled();expect(screen.getByText('附件待预览')).toBeVisible();act(()=>observers[0]([{isIntersecting:true} as IntersectionObserverEntry],{} as IntersectionObserver));act(()=>observers.slice(1).forEach(cb=>cb([{isIntersecting:true} as IntersectionObserverEntry],{} as IntersectionObserver)));expect(await screen.findByRole('img',{name:'一.png'})).toBeVisible();
});
it('ignores stale bytes after the immutable file version changes and releases its blob URL',async()=>{
 let finish!:(blob:Blob)=>void;api.thumbnail.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));const view=render(<InlineNodeAttachments node={node([file('a','image/png','一.png')])} onSize={vi.fn()} onPreview={vi.fn()}/>);await waitFor(()=>expect(api.thumbnail).toHaveBeenCalledTimes(1));view.rerender(<InlineNodeAttachments node={node([file('a','image/png','一.png',2)])} onSize={vi.fn()} onPreview={vi.fn()}/>);await screen.findByRole('img',{name:'一.png'});await act(async()=>finish(new Blob(['old'])));expect(URL.createObjectURL).toHaveBeenCalledTimes(1);view.unmount();expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:protected');
});
it('allows the original preview when a thumbnail fails',async()=>{
 api.thumbnail.mockRejectedValueOnce(new Error('没有访问权限'));const onPreview=vi.fn();render(<InlineNodeAttachments node={node([file('a','image/png','一.png',2)])} onSize={vi.fn()} onPreview={onPreview}/>);expect(await screen.findByText('缩略图无法读取，点击查看原图')).toBeVisible();fireEvent.click(screen.getByRole('button',{name:'查看图片 一.png'}));expect(onPreview).toHaveBeenCalledWith(expect.objectContaining({binding_id:'a',revision:2}));expect(api.content).not.toHaveBeenCalled();
});

it.each(['规则.docx','培训内容.pdf','数据.xlsx','文件没有后缀'])('shows %s as a compact name and previews only when clicked',name=>{
 const version=file('doc','application/octet-stream',name,3),onPreview=vi.fn();const {container}=render(<InlineNodeAttachments node={node([version])} onSize={vi.fn()} onPreview={onPreview}/>);const badge=screen.getByRole('button',{name:'预览文件 '+name});expect(badge).toHaveTextContent(name);expect(badge).toHaveAttribute('title',name);expect(api.content).not.toHaveBeenCalled();expect(container.querySelector('.kc-inline-preview')).toBeNull();fireEvent.click(badge);expect(onPreview).toHaveBeenCalledWith(version.version);expect(api.content).not.toHaveBeenCalled();
});
it('keeps the extension separate so truncation applies only to the long base name',()=>{const name='很长的培训资料名称'.repeat(12)+'.docx';render(<InlineNodeAttachments node={node([file('long','application/vnd.openxmlformats-officedocument.wordprocessingml.document',name)])} onSize={vi.fn()} onPreview={vi.fn()}/>);expect(document.querySelector('.kc-file-extension')).toHaveTextContent('.docx');expect(document.querySelector('.kc-file-name')).not.toHaveTextContent('.docx');});
