import { describe, expect, it } from 'vitest';
import { relationCurve } from '../relationGeometry';
describe('relation geometry',()=>{
  it('uses real card edges and center height for the final path',()=>{
    const curve=relationCurve({x:0,y:0,width:272,height:240},{x:400,y:20,width:300,height:200});
    expect(curve.start).toMatchObject({x:272,y:120});expect(curve.end).toMatchObject({x:400,y:120});
  });
  it.each([{x:-500,y:0,nx:-1,ny:0},{x:0,y:500,nx:0,ny:1},{x:0,y:-500,nx:0,ny:-1}])('faces the destination at $x $y',({x,y,nx,ny})=>{
    expect(relationCurve({x:0,y:0,width:272,height:170},{x,y,width:272,height:170}).start).toMatchObject({nx,ny});
  });
});

it('separates reverse and different-type relations into stable screen-sized lanes', async()=>{
  const {relationLane}=await import('../relationGeometry');
  const first={id:'1',container_id:'p',source_object_id:'a',target_object_id:'b',relation_type:'related'};
  const reverse={...first,id:'2',source_object_id:'b',target_object_id:'a'};
  const third={...first,id:'3',relation_type:'reference'};
  const edges=[third,reverse,first];
  expect(relationLane(edges,first,1)).toEqual({count:3,offset:0});
  expect(relationLane(edges,reverse,1)).toEqual({count:3,offset:-36});
  expect(relationLane(edges,third,.5)).toEqual({count:3,offset:-72});
  const a={x:0,y:0,width:272,height:170},b={...a,x:500};
  const paths=edges.map(r=>relationCurve(r===reverse?b:a,r===reverse?a:b,relationLane(edges,r,1).offset).path);
  expect(new Set(paths).size).toBe(3);
});

it('gives a newly created relationship the same lane before and after receiving its ID', async()=>{
  const {relationLane}=await import('../relationGeometry');
  const old={id:'existing',container_id:'p',source_object_id:'a',target_object_id:'b',relation_type:'reference'};
  const proposed={...old,id:'preview',relation_type:'related'};
  expect(relationLane([old],proposed,1).offset).toBe(relationLane([old],{...proposed,id:'000-real-id'},1).offset);
});

it.each([.2,.5,1,2])('keeps the preview and final parallel curve identical at zoom %s',async zoom=>{
  const {relationLane}=await import('../relationGeometry');
  const edge={id:'one',container_id:'p',source_object_id:'a',target_object_id:'b',relation_type:'related'};
  const edges=[edge,{...edge,id:'two',relation_type:'reference'}];
  const a={x:30,y:20,width:272,height:170},b={x:380,y:350,width:240,height:210};
  const screen=(box:typeof a)=>({x:box.x*zoom+60,y:box.y*zoom+95,width:box.width*zoom,height:box.height*zoom});
  const final=relationCurve(a,b,relationLane(edges,edge,zoom).offset).path.match(/-?\d+(?:\.\d+)?/g)!.map(Number);
  const preview=relationCurve(screen(a),screen(b),relationLane(edges,edge,1).offset,zoom).path.match(/-?\d+(?:\.\d+)?/g)!.map(Number);
  for(let i=0;i<final.length;i++)expect(preview[i]).toBeCloseTo(final[i]*zoom+(i%2===0?60:95));
});

it('moves the visible curve midpoint continuously with its bend',()=>{
 const a={x:0,y:0,width:200,height:100},b={x:500,y:0,width:200,height:100};const straight=relationCurve(a,b,0),bent=relationCurve(a,b,100);expect(bent.middle.y-straight.middle.y).toBe(75);expect(bent.path).not.toBe(straight.path);
});

it('retains normalized edge anchors as cards resize, with independently movable controls',()=>{
 const a={x:10,y:20,width:100,height:50},b={x:400,y:200,width:200,height:100},appearance={source_anchor:{x:.3,y:0},target_anchor:{x:1,y:.7},source_control:{x:30,y:-100},target_control:{x:80,y:120}};
 const c=relationCurve(a,b,0,1,appearance);expect(c.start).toMatchObject({x:40,y:20});expect(c.end).toMatchObject({x:600,y:270});expect(c.control1).toEqual({x:70,y:-80});expect(c.control2).toEqual({x:680,y:390});
 const resized=relationCurve({...a,width:200},b,0,1,appearance);expect(resized.start.x).toBe(70);expect(resized.control2).toEqual(c.control2);
 const scaled=relationCurve({x:20,y:40,width:200,height:100},{x:800,y:400,width:400,height:200},0,2,appearance);expect(scaled.control1).toEqual({x:140,y:-160});expect(scaled.middle.x).toBeCloseTo(c.middle.x*2);
});
it('projects free endpoints onto the nearest border including corners',async()=>{
 const {nearestAnchor}=await import('../relationGeometry');const b={x:10,y:20,width:100,height:50};expect(nearestAnchor(b,{x:45,y:18})).toEqual({x:.35,y:0});expect(nearestAnchor(b,{x:200,y:40})).toEqual({x:1,y:.4});expect(nearestAnchor(b,{x:-100,y:200})).toEqual({x:0,y:1});
});

it('moves anchors continuously across corners with keyboard arrows',async()=>{const {stepAnchor}=await import('../relationGeometry');const box={x:0,y:0,width:100,height:50};expect(stepAnchor(box,{x:.98,y:0},5)).toEqual({x:1,y:.06});expect(stepAnchor(box,{x:0,y:0},-5)).toEqual({x:0,y:expect.closeTo(.1)});});

it.each([
 {source_control:{x:1400,y:-1800},target_control:{x:-1600,y:1900}},
 {source_control:{x:0,y:-2400},target_control:{x:0,y:-2400}},
 {source_control:{x:-1900,y:0},target_control:{x:2200,y:0}},
])('bounds every point on a freely controlled cubic (%j)',async appearance=>{
 const {curveBounds}=await import('../relationGeometry');
 const curve=relationCurve({x:10,y:20,width:100,height:48},{x:340,y:220,width:180,height:90},36,1,appearance);
 const bounds=curveBounds(curve);
 for(let i=0;i<=1000;i++){
  const t=i/1000,q=1-t;
  const at=(axis:'x'|'y')=>q**3*curve.start[axis]+3*q*q*t*curve.control1[axis]+3*q*t*t*curve.control2[axis]+t**3*curve.end[axis];
  expect(at('x')).toBeGreaterThanOrEqual(bounds.x-1e-7);expect(at('x')).toBeLessThanOrEqual(bounds.x+bounds.width+1e-7);
  expect(at('y')).toBeGreaterThanOrEqual(bounds.y-1e-7);expect(at('y')).toBeLessThanOrEqual(bounds.y+bounds.height+1e-7);
 }
});
