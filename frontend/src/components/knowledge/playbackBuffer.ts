export type BufferStatus={kind:'buffering'|'paused'|'playing'|'blocked';ahead:number};
export function bufferedAhead(video:HTMLVideoElement){
 for(let i=0;i<video.buffered.length;i++)if(video.buffered.start(i)<=video.currentTime+.08&&video.buffered.end(i)>video.currentTime)return video.buffered.end(i)-video.currentTime;
 return 0;
}
/** Hold startup/rebuffering until a continuous runway exists; never bridge seek gaps. */
export class PlaybackBuffer{
 private alive=true;private waiting=true;private target=4;private programmaticPause=false;private starting=false;private positioned=false;
 constructor(private video:HTMLVideoElement,private report:(status:BufferStatus)=>void,private intent:(wanted:boolean)=>void,private startAt=0,private wanted=true){this.intent(wanted);this.report({kind:wanted?'buffering':'paused',ahead:0});}
 private enough(ahead:number){const left=this.video.duration-this.video.currentTime;return this.video.readyState>=2&&(ahead>=this.target||(Number.isFinite(left)&&left>=0&&ahead+.12>=left));}
 private hold(){if(!this.video.paused){this.programmaticPause=true;this.video.pause();}}
 update(){
  if(!this.alive)return;const ahead=bufferedAhead(this.video);
  if(!this.wanted){this.report({kind:'paused',ahead});return;}
  if(!this.waiting){this.report({kind:'playing',ahead});return;}
  this.report({kind:'buffering',ahead});
  if(!this.enough(ahead)||this.starting)return;
  this.waiting=false;this.starting=true;
  void Promise.resolve().then(()=>{if(this.alive&&this.wanted)return this.video.play();}).catch(()=>{if(this.alive){this.wanted=false;this.intent(false);this.report({kind:'blocked',ahead:bufferedAhead(this.video)});}}).finally(()=>{this.starting=false;if(this.alive&&this.waiting&&this.wanted)this.update();});
 }
 metadata(){if(!this.positioned&&Number.isFinite(this.video.duration)){this.positioned=true;if(this.startAt>0)this.video.currentTime=Math.min(this.startAt,Math.max(0,this.video.duration-.1));}this.update();}
 play(){if(!this.alive)return;this.wanted=true;this.intent(true);if(this.waiting&&!this.enough(bufferedAhead(this.video)))this.hold();else this.waiting=false;this.update();}
 pause(){if(!this.alive)return;if(this.programmaticPause){this.programmaticPause=false;return;}this.wanted=false;this.intent(false);this.update();}
 wait(){if(!this.alive||!this.wanted||this.video.ended)return;this.waiting=true;this.target=6;this.hold();this.update();}
 seek(){if(!this.alive)return;this.waiting=true;if(this.wanted)this.hold();this.update();}
 cancel(){this.wanted=false;this.intent(false);this.hold();this.report({kind:'paused',ahead:bufferedAhead(this.video)});}
 dispose(){this.alive=false;this.wanted=false;this.video.pause();this.video.removeAttribute('src');this.video.load();}
}
