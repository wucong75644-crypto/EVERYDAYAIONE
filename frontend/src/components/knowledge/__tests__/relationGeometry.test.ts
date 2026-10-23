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
