import {describe,it,expect} from 'vitest';
import {toolsPosition} from '../structureDirection';
describe('measured node toolbar placement',()=>{
 it.each([{width:430,height:44},{width:160,height:88},{width:600,height:60}])('keeps the real $width × $height surface clear of the card and ports',size=>{
  const card={x:300,y:230,width:130,height:48},area={width:1100,height:700},ports=[{x:455,y:254,radius:12}];
  const p=toolsPosition(card,area,ports,size);
  expect(p.left).toBeGreaterThanOrEqual(12);expect(p.left+size.width).toBeLessThanOrEqual(area.width-12);
  expect(p.top+size.height<=card.y-4||p.top>=card.y+card.height+4||p.left+size.width<=card.x-4||p.left>=card.x+card.width+4).toBe(true);
 });
 it('finds a free edge beyond an adjacent branch instead of covering it',()=>{
  const card={x:300,y:300,width:100,height:60},other={x:250,y:100,width:500,height:360},size={width:420,height:70};
  const p=toolsPosition(card,{width:1000,height:800},[],size,[other]);
  expect(p.top).toBeGreaterThanOrEqual(other.y+other.height+4);
 });
});
