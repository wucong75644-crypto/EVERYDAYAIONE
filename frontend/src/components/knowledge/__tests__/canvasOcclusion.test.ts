import {describe,it,expect} from 'vitest';
import {usableCanvas,revealScreenRect,fitSafeView} from '../canvasOcclusion';
import {toolsPosition} from '../structureDirection';
describe('shared canvas occlusion geometry',()=>{
 const area={width:700,height:700},surfaces=[{x:12,y:56,width:676,height:112},{x:20,y:175,width:600,height:90},{x:20,y:650,width:600,height:30}],safe=usableCanvas(area,surfaces);
 it('reserves wrapped toolbar, feedback and bottom hints without adding their heights twice',()=>{expect(safe).toEqual({x:0,y:277,width:700,height:361});});
 it('reveals a node below the real top surfaces',()=>{const next=revealScreenRect({x:0,y:0,z:1},{x:200,y:80,width:120,height:48},safe);expect(next.y+80).toBeGreaterThanOrEqual(safe.y+12);});
 it('positions a measured toolbar outside protected surfaces',()=>{const p=toolsPosition({x:200,y:300,width:100,height:48},{...area,top:safe.y},[],{width:280,height:44},surfaces);expect(p.top).toBeGreaterThanOrEqual(safe.y);});
 it('fits the complete world bounds inside the usable canvas',()=>{const bounds={x:-1000,y:-2000,width:3000,height:4000},v=fitSafeView([bounds],safe)!;expect(bounds.x*v.z+v.x).toBeGreaterThanOrEqual(safe.x);expect(bounds.y*v.z+v.y).toBeGreaterThanOrEqual(safe.y);expect((bounds.y+bounds.height)*v.z+v.y).toBeLessThanOrEqual(safe.y+safe.height);});
 it('does not move an already visible caret or attempt impossible tiny areas',()=>{const view={x:20,y:30,z:.5};expect(revealScreenRect(view,{x:300,y:400,width:2,height:24},safe)).toBe(view);expect(revealScreenRect(view,{x:0,y:0,width:2,height:24},{x:0,y:0,width:10,height:10})).toBe(view);});
});
