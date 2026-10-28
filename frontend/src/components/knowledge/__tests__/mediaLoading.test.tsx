import {act,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {scheduleThumbnail} from '../mediaLoading';
import {KnowledgeMediaPlayer} from '../KnowledgeMediaPlayer';
import {useKnowledgeThumbnail,useNearViewport} from '../useKnowledgeThumbnail';
const api=vi.hoisted(()=>({playback:vi.fn(),thumbnail:vi.fn()}));
const auth=vi.hoisted(()=>({user:{id:'u'},currentOrgId:'org'}));
vi.mock('../../../services/knowledge',()=>({knowledgeFilesApi:api}));
vi.mock('../../../stores/useAuthStore',()=>({useAuthStore:(select:(s:typeof auth)=>unknown)=>select(auth)}));
const version={binding_id:'file',revision:2,name:'培训.mp4'};
beforeEach(()=>{vi.resetAllMocks();vi.stubGlobal('IntersectionObserver',undefined);api.playback.mockResolvedValue({url:'/private/version/stream',expires_in:300});api.thumbnail.mockResolvedValue(new Blob(['thumb']));URL.createObjectURL=vi.fn(()=> 'blob:thumbnail');URL.revokeObjectURL=vi.fn();vi.spyOn(HTMLMediaElement.prototype,'pause').mockImplementation(()=>{});vi.spyOn(HTMLMediaElement.prototype,'load').mockImplementation(()=>{});auth.user.id='u';});
afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals();});
it('bounds outstanding thumbnails at four and cancels queued work',async()=>{
 const finishes:Array<()=>void>=[],work=vi.fn(()=>new Promise<void>(resolve=>finishes.push(resolve))),controllers=Array.from({length:8},()=>new AbortController());
 const requests=controllers.map(c=>scheduleThumbnail(work,c.signal).catch(e=>e.name));await Promise.resolve();expect(work).toHaveBeenCalledTimes(4);controllers[4].abort();finishes.splice(0).forEach(done=>done());await waitFor(()=>expect(work).toHaveBeenCalledTimes(7));finishes.splice(0).forEach(done=>done());expect((await Promise.all(requests))[4]).toBe('AbortError');
});
it('keeps one player active even when two views display the same file',async()=>{
 const view=render(<><KnowledgeMediaPlayer objectId="node" version={version}/><KnowledgeMediaPlayer objectId="node" version={version}/></>);const buttons=screen.getAllByRole('button',{name:'播放视频 培训.mp4'});fireEvent.click(buttons[0]);const old=await screen.findByLabelText('培训.mp4');fireEvent.click(screen.getByRole('button',{name:'播放视频 培训.mp4'}));await waitFor(()=>expect(api.playback).toHaveBeenCalledTimes(2));expect(screen.getAllByLabelText('培训.mp4')).toHaveLength(1);expect(old).not.toHaveAttribute('src');view.unmount();
});
it('unloads offscreen video and requires a new click on return',async()=>{
 let observer!:IntersectionObserverCallback;vi.stubGlobal('IntersectionObserver',class{constructor(cb:IntersectionObserverCallback){observer=cb;}observe(){}disconnect(){}});render(<KnowledgeMediaPlayer objectId="node" version={version}/>);act(()=>observer([{isIntersecting:true} as IntersectionObserverEntry],{} as IntersectionObserver));fireEvent.click(screen.getByRole('button',{name:'播放视频 培训.mp4'}));const video=await screen.findByLabelText('培训.mp4');act(()=>observer([{isIntersecting:false} as IntersectionObserverEntry],{} as IntersectionObserver));expect(video).not.toHaveAttribute('src');act(()=>observer([{isIntersecting:true} as IntersectionObserverEntry],{} as IntersectionObserver));expect(api.playback).toHaveBeenCalledTimes(1);expect(screen.queryByLabelText('培训.mp4')).toBeNull();
});
it('drops playback immediately when the account changes',async()=>{
 const view=render(<KnowledgeMediaPlayer objectId="node" version={version}/>);fireEvent.click(screen.getByRole('button',{name:'播放视频 培训.mp4'}));const video=await screen.findByLabelText('培训.mp4');auth.user.id='other';view.rerender(<KnowledgeMediaPlayer objectId="node" version={version}/>);expect(video).not.toHaveAttribute('src');expect(api.playback).toHaveBeenCalledTimes(1);
});
it('retries failed playback with one click without a public fallback',async()=>{
 api.playback.mockRejectedValueOnce(new Error('撤权'));render(<KnowledgeMediaPlayer objectId="node" version={version}/>);fireEvent.click(screen.getByRole('button',{name:'播放视频 培训.mp4'}));await screen.findByRole('alert');fireEvent.click(screen.getByRole('button',{name:'播放视频 培训.mp4'}));expect(await screen.findByLabelText('培训.mp4')).toHaveAttribute('src','/private/version/stream');
});
function ImageProbe(){const {ref,near}=useNearViewport(),image=useKnowledgeThumbnail('node','image',1,near);return <div ref={ref}>{image.src&&<img alt="缩略图" src={image.src}/>}</div>;}
it('aborts offscreen thumbnail requests and releases decoded object URLs',async()=>{
 let observer!:IntersectionObserverCallback;vi.stubGlobal('IntersectionObserver',class{constructor(cb:IntersectionObserverCallback){observer=cb;}observe(){}disconnect(){}});render(<ImageProbe/>);expect(api.thumbnail).not.toHaveBeenCalled();act(()=>observer([{isIntersecting:true} as IntersectionObserverEntry],{} as IntersectionObserver));await screen.findByAltText('缩略图');const signal=api.thumbnail.mock.calls[0][3] as AbortSignal;act(()=>observer([{isIntersecting:false} as IntersectionObserverEntry],{} as IntersectionObserver));expect(signal.aborted).toBe(true);expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:thumbnail');expect(screen.queryByAltText('缩略图')).toBeNull();
});
it('renews a running lease without replacing the media source or leaking timers',async()=>{
 vi.useFakeTimers();const view=render(<KnowledgeMediaPlayer objectId="node" version={version}/>);await act(async()=>fireEvent.click(screen.getByRole('button',{name:'播放视频 培训.mp4'})));const video=screen.getByLabelText('培训.mp4');expect(video).toHaveAttribute('src','/private/version/stream');await act(async()=>vi.advanceTimersByTimeAsync(240000));expect(api.playback).toHaveBeenCalledTimes(2);expect(screen.getByLabelText('培训.mp4')).toBe(video);view.unmount();expect(vi.getTimerCount()).toBe(0);
});
