import { describe, expect, it } from 'vitest';
import { wheelView, zoomAt, edgeVelocity, fitView } from '../viewportGestures';
const view={x:60,y:95,z:1};
const wheel={deltaX:0,deltaY:0,deltaMode:0,ctrlKey:false,metaKey:false,shiftKey:false};
describe('viewport gestures',()=>{
 it('pans both axes and supports line/page wheel deltas',()=>{
  expect(wheelView(view,{...wheel,deltaX:40,deltaY:90},{x:0,y:0},600)).toEqual({x:20,y:5,z:1});
  expect(wheelView(view,{...wheel,deltaY:2,deltaMode:1},{x:0,y:0},600).y).toBe(63);
  expect(wheelView(view,{...wheel,deltaY:1,deltaMode:2},{x:0,y:0},600).y).toBe(-505);
  expect(wheelView(view,{...wheel,deltaY:30,shiftKey:true},{x:0,y:0},600)).toEqual({x:30,y:95,z:1});
 });
 it.each(['ctrlKey','metaKey'])('zooms at pointer for %s rather than panning',key=>{
  const anchor={x:300,y:250};const next=wheelView(view,{...wheel,deltaY:-100,[key]:true},anchor,600);
  expect(next.z).toBeGreaterThan(1);expect((anchor.x-next.x)/next.z).toBeCloseTo(anchor.x-view.x);expect((anchor.y-next.y)/next.z).toBeCloseTo(anchor.y-view.y);
 });
 it('has no phantom zoom for horizontal scroll and keeps reset center stable',()=>{
  expect(wheelView(view,{...wheel,deltaX:50},{x:0,y:0},600)).toEqual({...view,x:10});
  expect(zoomAt({x:0,y:0,z:.5},1,{x:400,y:300})).toEqual({x:-400,y:-300,z:1});
 });
 it('edge speed scales with proximity and stops outside',()=>{
  expect(edgeVelocity(400,0,800)).toBe(0);expect(edgeVelocity(0,0,800)).toBe(420);expect(edgeVelocity(800,0,800)).toBe(-420);expect(edgeVelocity(801,0,800)).toBe(0);
 });
 it('fits actual card bounds with margins and supports empty/small canvases',()=>{
  expect(fitView([],{width:900,height:600})).toBeNull();
  const next=fitView([{x:-200,y:100,width:300,height:400},{x:1000,y:700,width:240,height:116}],{width:900,height:600})!;
  expect(next.z).toBeLessThan(1);expect(-200*next.z+next.x).toBeGreaterThanOrEqual(40);expect(1240*next.z+next.x).toBeLessThanOrEqual(860);
  expect(816*next.z+next.y).toBeLessThanOrEqual(536);
 });
 it('fits very distant cards below normal manual zoom minimum without clipping',()=>{
  const next=fitView([{x:-1000000,y:0,width:240,height:120},{x:1000000,y:0,width:240,height:120}],{width:900,height:600})!;
  expect(next.z).toBeLessThan(.02);expect(1000240*next.z+next.x).toBeLessThanOrEqual(860.001);
  expect(zoomAt(next,next.z*1.2,{x:450,y:300}).z).toBeCloseTo(next.z*1.2);
 });

});
