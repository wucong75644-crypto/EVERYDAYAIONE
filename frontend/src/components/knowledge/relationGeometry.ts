import type { KnowledgeRelation, RelationAppearance, RelationPoint } from '../../services/knowledge';
export type RelationBox = { x: number; y: number; width: number; height: number };
type Port = { x: number; y: number; nx: number; ny: number };
export function nearestAnchor(box: RelationBox, point: RelationPoint): RelationPoint {
  const x=Math.max(0,Math.min(1,(point.x-box.x)/Math.max(1,box.width))), y=Math.max(0,Math.min(1,(point.y-box.y)/Math.max(1,box.height)));
  const sides=[{distance:x*box.width,point:{x:0,y}},{distance:(1-x)*box.width,point:{x:1,y}},{distance:y*box.height,point:{x,y:0}},{distance:(1-y)*box.height,point:{x,y:1}}];
  return sides.sort((a,b)=>a.distance-b.distance)[0].point;
}
/** Travel around the border; arrows remain useful at corners and on every side. */
export function stepAnchor(box:RelationBox,point:RelationPoint,step:number):RelationPoint {
 const w=Math.max(1,box.width),h=Math.max(1,box.height),length=2*(w+h);
 let t=point.y===0?point.x*w:point.x===1?w+point.y*h:point.y===1?w+h+(1-point.x)*w:2*w+h+(1-point.y)*h;
 t=((t+step)%length+length)%length;
 return t<=w?{x:t/w,y:0}:t<=w+h?{x:1,y:(t-w)/h}:t<=2*w+h?{x:1-(t-w-h)/w,y:1}:{x:0,y:1-(t-2*w-h)/h};
}
function anchored(box:RelationBox,p:RelationPoint):Port {
 return {x:box.x+box.width*p.x,y:box.y+box.height*p.y,nx:p.x===0?-1:p.x===1?1:0,ny:p.x!==0&&p.x!==1?(p.y===0?-1:1):0};
}
export function relationCurve(a: RelationBox, b: RelationBox, lane = 0, scale = 1, appearance?:Partial<RelationAppearance>) {
  const ax = a.x + a.width / 2, ay = a.y + a.height / 2;
  const bx = b.x + b.width / 2, by = b.y + b.height / 2;
  const horizontal = Math.abs(bx - ax) / Math.max(1, a.width + b.width) >= Math.abs(by - ay) / Math.max(1, a.height + b.height);
  let start: Port, end: Port;
  if (horizontal) {
    const direction = bx >= ax ? 1 : -1;
    start = { x: direction > 0 ? a.x + a.width : a.x, y: ay, nx: direction, ny: 0 };
    end = { x: direction > 0 ? b.x : b.x + b.width, y: by, nx: -direction, ny: 0 };
  } else {
    const direction = by >= ay ? 1 : -1;
    start = { x: ax, y: direction > 0 ? a.y + a.height : a.y, nx: 0, ny: direction };
    end = { x: bx, y: direction > 0 ? b.y : b.y + b.height, nx: 0, ny: -direction };
  }
  if(appearance?.source_anchor)start=anchored(a,appearance.source_anchor);
  if(appearance?.target_anchor)end=anchored(b,appearance.target_anchor);
  const reach = Math.max(32 * scale, Math.min(160 * scale, Math.hypot(end.x - start.x, end.y - start.y) * .4));
  const length = Math.hypot(bx - ax, by - ay) || 1;
  const ox = -(by - ay) / length * lane, oy = (bx - ax) / length * lane;
  const control1 = appearance?.source_control ? {x:start.x+appearance.source_control.x*scale,y:start.y+appearance.source_control.y*scale} : {x:start.x+start.nx*reach+ox,y:start.y+start.ny*reach+oy};
  const control2 = appearance?.target_control ? {x:end.x+appearance.target_control.x*scale,y:end.y+appearance.target_control.y*scale} : {x:end.x+end.nx*reach+ox,y:end.y+end.ny*reach+oy};
  const path = `M ${start.x} ${start.y} C ${control1.x} ${control1.y}, ${control2.x} ${control2.y}, ${end.x} ${end.y}`;
  const middle = { x:(start.x+3*control1.x+3*control2.x+end.x)/8,y:(start.y+3*control1.y+3*control2.y+end.y)/8 };
  return { start, end, control1, control2, path, middle, normal: {x:-(by-ay)/length,y:(bx-ax)/length} };
}

export function relationLane(relations: KnowledgeRelation[], relation: KnowledgeRelation, zoom: number) {
  const a = relation.source_object_id, b = relation.target_object_id;
  const group = [...relations.filter(r => r.id !== relation.id), relation].filter(r => (r.source_object_id === a && r.target_object_id === b) || (r.source_object_id === b && r.target_object_id === a)).sort((x, y) => x.source_object_id.localeCompare(y.source_object_id) || x.target_object_id.localeCompare(y.target_object_id) || x.relation_type.localeCompare(y.relation_type) || x.id.localeCompare(y.id));
  const direction = a < b ? 1 : -1;
  return { count: group.length, offset: (group.findIndex(r => r.id === relation.id) - (group.length - 1) / 2) * 36 / zoom * direction };
}
export const relationTypes: Record<string, string> = { reference: '引用', related: '关联', dependency: '依赖' };

/** Exact cubic extrema, rather than its midpoint or oversized control polygon. */
export function curveBounds(curve:ReturnType<typeof relationCurve>){
 const extrema=(p0:number,p1:number,p2:number,p3:number)=>{
  const a=-p0+3*p1-3*p2+p3,b=2*(p0-2*p1+p2),c=p1-p0;
  const roots:number[]=[];
  if(Math.abs(a)<1e-9){if(Math.abs(b)>1e-9)roots.push(-c/b);}
  else {const d=b*b-4*a*c;if(d>=0)roots.push((-b+Math.sqrt(d))/(2*a),(-b-Math.sqrt(d))/(2*a));}
  return [p0,p3,...roots.filter(t=>t>0&&t<1).map(t=>(1-t)**3*p0+3*(1-t)**2*t*p1+3*(1-t)*t*t*p2+t**3*p3)];
 };
 const xs=extrema(curve.start.x,curve.control1.x,curve.control2.x,curve.end.x),ys=extrema(curve.start.y,curve.control1.y,curve.control2.y,curve.end.y);
 const x=Math.min(...xs),y=Math.min(...ys);return {x,y,width:Math.max(...xs)-x,height:Math.max(...ys)-y};
}
