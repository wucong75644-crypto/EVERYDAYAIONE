import type {Direction} from './structureDirection';
type Box={x:number;y:number;width:number;height:number};
/** Shared rounded elbow for a standard mindmap branch; manual anchors keep their own curve. */
export function mindmapConnector(source:Box,target:Box,direction:Direction){
 const horizontal=direction==='right'||direction==='left',forward=direction==='right'||direction==='down';
 const sx=horizontal?source.x+(forward?source.width:0):source.x+source.width/2,sy=horizontal?source.y+source.height/2:source.y+(forward?source.height:0);
 const ex=horizontal?target.x+(forward?0:target.width):target.x+target.width/2,ey=horizontal?target.y+target.height/2:target.y+(forward?0:target.height);
 const [u,v,endU,endV]=horizontal?[sx,sy,ex,ey]:[sy,sx,ey,ex],mid=(u+endU)/2,step=Math.sign(endU-u)||1,cross=Math.sign(endV-v)||1;
 const radius=Math.min(10,Math.abs(endU-u)/4,Math.abs(endV-v)/2),point=(a:number,b:number)=>horizontal?`${a} ${b}`:`${b} ${a}`;
 if(!radius)return `M ${sx} ${sy} L ${ex} ${ey}`;
 return `M ${sx} ${sy} L ${point(mid-step*radius,v)} Q ${point(mid,v)} ${point(mid,v+cross*radius)} L ${point(mid,endV-cross*radius)} Q ${point(mid,endV)} ${point(mid+step*radius,endV)} L ${ex} ${ey}`;
}
