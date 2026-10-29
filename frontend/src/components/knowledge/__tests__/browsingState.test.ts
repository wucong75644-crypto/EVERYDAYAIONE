import {beforeEach,expect,it,vi} from 'vitest';
import {restoreBrowsingState,rememberBrowsingState} from '../browsingState';
import type {KnowledgeScene} from '../../../services/knowledge';
const scene={containers:[{id:'a'},{id:'b'}],nodes:[{id:'one',container_id:'a'},{id:'two',container_id:'b'}]} as KnowledgeScene;
const key='knowledge-browsing-state:v1:u:org';
beforeEach(()=>{vi.restoreAllMocks();localStorage.clear();});
it('stores both open and closed choices separately for each authorized space',()=>{
 rememberBrowsingState('u:org',scene,new Set(['one']),{one:false,two:true,'container:a':false,'container:b':true});
 const saved=JSON.parse(localStorage.getItem(key)!);expect(saved.spaces.a).toEqual({folded:['one'],directory:{one:false,'container:a':false}});expect(saved.spaces.b).toEqual({folded:[],directory:{two:true,'container:b':true}});
 expect(restoreBrowsingState('u:org',scene)).toEqual({folded:new Set(['one']),directory:{one:false,two:true,'container:a':false,'container:b':true}});
 rememberBrowsingState('u:org',scene,new Set(),{});expect(restoreBrowsingState('u:org',scene)).toEqual({folded:new Set(),directory:{}});
});
it('isolates identities and ignores revoked spaces, deleted nodes and mismatched ownership',()=>{
 localStorage.setItem(key,JSON.stringify({version:1,spaces:{a:{folded:['one','two','gone'],directory:{one:false,two:false,gone:true,'container:a':false,'container:b':false}},b:{folded:['two'],directory:{two:true}}}}));
 expect(restoreBrowsingState('different:org',scene).folded.size).toBe(0);expect(restoreBrowsingState('u:other',scene).folded.size).toBe(0);
 const authorized={...scene,containers:scene.containers.slice(0,1)};expect(restoreBrowsingState('u:org',authorized)).toEqual({folded:new Set(['one']),directory:{one:false,'container:a':false}});
 expect(restoreBrowsingState('u:org',{...authorized,nodes:[]})).toEqual({folded:new Set(),directory:{'container:a':false}});
});
it('does not serialize absent or unauthorized content IDs',()=>{
 rememberBrowsingState('u:org',{...scene,containers:scene.containers.slice(0,1)},new Set(['one','two','gone']),{one:false,two:true,gone:false,'container:b':true});
 expect(JSON.parse(localStorage.getItem(key)!)).toEqual({version:1,spaces:{a:{folded:['one'],directory:{one:false}}}});
});
it.each(['{bad','null','[]','{"version":2,"spaces":{}}','{"version":1,"spaces":null}','{"version":1,"spaces":{"a":{"folded":{},"directory":{"one":"false"}}}}'])('safely defaults for invalid persisted state %s',raw=>{
 localStorage.setItem(key,raw);expect(restoreBrowsingState('u:org',scene)).toEqual({folded:new Set(),directory:{}});
});
it('keeps browsing usable when local storage is blocked',()=>{
 vi.spyOn(Storage.prototype,'getItem').mockImplementation(()=>{throw new Error('blocked');});vi.spyOn(Storage.prototype,'setItem').mockImplementation(()=>{throw new Error('quota');});
 expect(restoreBrowsingState('u:org',scene).folded.size).toBe(0);expect(()=>rememberBrowsingState('u:org',scene,new Set(['one']),{})).not.toThrow();
});
