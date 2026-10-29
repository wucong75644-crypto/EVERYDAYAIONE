import {expect,it,vi} from 'vitest';
import {PlaybackBuffer,bufferedAhead} from '../playbackBuffer';
function fixture(startAt=0,resume=true){
 let ranges:number[][]=[];
 const video={currentTime:0,duration:15,readyState:3,paused:true,ended:false,buffered:{get length(){return ranges.length;},start:(i:number)=>ranges[i][0],end:(i:number)=>ranges[i][1]},pause:vi.fn(()=>{video.paused=true;}),play:vi.fn(async()=>{video.paused=false;}),removeAttribute:vi.fn(),load:vi.fn()};
 const report=vi.fn(),intent=vi.fn(),buffer=new PlaybackBuffer(video as unknown as HTMLVideoElement,report,intent,startAt,resume);
 return {video,buffer,report,intent,setRanges:(value:number[][])=>{ranges=value;}};
}
it('holds startup until four seconds are continuously buffered',async()=>{
 const f=fixture();f.setRanges([[0,1]]);f.buffer.metadata();await Promise.resolve();expect(f.video.play).not.toHaveBeenCalled();expect(f.report).toHaveBeenLastCalledWith({kind:'buffering',ahead:1});f.setRanges([[0,4.1]]);f.buffer.update();await Promise.resolve();expect(f.video.play).toHaveBeenCalledTimes(1);
});
it('waits for a larger runway after an underrun instead of restarting on every frame',async()=>{
 const f=fixture();f.setRanges([[0,5]]);f.buffer.update();await Promise.resolve();f.video.currentTime=4.9;f.buffer.wait();f.buffer.pause();expect(f.video.pause).toHaveBeenCalled();f.setRanges([[0,6]]);f.buffer.update();await Promise.resolve();expect(f.video.play).toHaveBeenCalledTimes(1);f.setRanges([[0,11]]);f.buffer.update();await vi.waitFor(()=>expect(f.video.play).toHaveBeenCalledTimes(2));
});
it('does not resume after the user pauses or cancels buffering',async()=>{
 const f=fixture();f.buffer.cancel();f.setRanges([[0,15]]);f.buffer.update();await Promise.resolve();expect(f.video.play).not.toHaveBeenCalled();expect(f.intent).toHaveBeenLastCalledWith(false);
 const g=fixture();g.setRanges([[0,5]]);g.buffer.update();await Promise.resolve();g.video.paused=true;g.buffer.pause();g.buffer.wait();g.setRanges([[0,15]]);g.buffer.update();await Promise.resolve();expect(g.video.play).toHaveBeenCalledTimes(1);
});
it('does not count disjoint ranges beyond a seek gap as playable buffer',async()=>{
 const f=fixture();f.video.currentTime=6;f.setRanges([[0,4],[10,14]]);expect(bufferedAhead(f.video as unknown as HTMLVideoElement)).toBe(0);f.buffer.seek();await Promise.resolve();expect(f.video.play).not.toHaveBeenCalled();
});
it('starts a short remaining tail without waiting for impossible extra seconds',async()=>{
 const f=fixture();f.video.currentTime=14;f.setRanges([[14,15]]);f.buffer.update();await Promise.resolve();expect(f.video.play).toHaveBeenCalledTimes(1);
});
it('restores the viewing position and preserves a paused quality switch',async()=>{
 const f=fixture(7,false);f.setRanges([[0,12]]);f.buffer.metadata();expect(f.video.currentTime).toBe(7);await Promise.resolve();expect(f.video.play).not.toHaveBeenCalled();f.video.paused=false;f.buffer.play();expect(f.intent).toHaveBeenLastCalledWith(true);expect(f.report).toHaveBeenLastCalledWith({kind:'playing',ahead:5});
});
it('surfaces autoplay policy rejection and allows a native play gesture',async()=>{
 const f=fixture();f.video.play.mockRejectedValueOnce(new Error('NotAllowedError'));f.setRanges([[0,15]]);f.buffer.update();await vi.waitFor(()=>expect(f.report).toHaveBeenLastCalledWith({kind:'blocked',ahead:15}));f.video.paused=false;f.buffer.play();expect(f.report).toHaveBeenLastCalledWith({kind:'playing',ahead:15});
});
it('cancels queued play and releases the source on removal',async()=>{
 const f=fixture();f.setRanges([[0,15]]);f.buffer.update();f.buffer.dispose();await Promise.resolve();expect(f.video.play).not.toHaveBeenCalled();expect(f.video.removeAttribute).toHaveBeenCalledWith('src');expect(f.video.load).toHaveBeenCalled();
});
