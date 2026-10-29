import {expect,it} from 'vitest';
import {mindmapConnector} from '../mindmapConnector';
import {defaultStyle,nodeStyle,readableTopicColor} from '../canvasStyle';
it.each(['right','left','up','down'] as const)('connects topic edges with finite rounded elbows toward %s',direction=>{
 const result=mindmapConnector({x:100,y:100,width:80,height:48},{x:400,y:300,width:100,height:60},direction);
 expect(result).not.toMatch(/NaN|Infinity/);expect(result).toContain(' Q ');
 const starts={right:'M 180 124',left:'M 100 124',up:'M 140 100',down:'M 140 148'};
 const ends={right:'L 400 330',left:'L 500 330',up:'L 450 360',down:'L 450 300'};
 expect(result.startsWith(starts[direction])).toBe(true);expect(result.endsWith(ends[direction])).toBe(true);
});
it('keeps aligned branches straight',()=>expect(mindmapConnector({x:0,y:0,width:80,height:48},{x:300,y:0,width:80,height:48},'right')).toBe('M 80 24 L 300 24'));
it('makes inherited legacy text readable without overriding explicitly chosen foregrounds',()=>{
 expect(readableTopicColor({...defaultStyle,background:'#000000'})).toBe('#ffffff');
 expect(nodeStyle({style:{...defaultStyle,background:'#2634df'}}).color).toBe('#ffffff');
 expect(readableTopicColor({...defaultStyle,background:'#ff8000'})).toBe(defaultStyle.color);
 expect(readableTopicColor({...defaultStyle,background:'#000000',color:'#efefef'})).toBe('#efefef');
});
