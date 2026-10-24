import { describe, it, expect } from 'vitest';
import { historyTargets } from '../historyTargets';
import type { KnowledgeNode, KnowledgeScene } from '../../../services/knowledge';
const root: KnowledgeNode = { id: 'r', container_id: 'p', parent_id: null, kind: 'topic', title: 'root', revision: 1, layout_revision: 1, node_id: 'nr', x: 0, y: 0 };
const child = { ...root, id: 'a', node_id: 'na', parent_id: 'r', x: 330 };
const grandchild = { ...child, id: 'b', node_id: 'nb', parent_id: 'a', x: 660 };
const scene = (nodes: KnowledgeNode[], relations: KnowledgeScene['relations'] = []): KnowledgeScene => ({ containers: [], nodes, relations });
describe('history focus targets', () => {
  it('selects the restored branch root, including restored descendants through that root', () => expect(historyTargets(scene([root]), scene([root, child, grandchild]), 'p').map(n => n.id)).toEqual(['a']));
  it('locates the surviving parent after deleting a whole branch', () => expect(historyTargets(scene([root, child, grandchild]), scene([root]), 'p').map(n => n.id)).toEqual(['r']));
  it('selects all moved independent branches and ignores version counters', () => expect(historyTargets(scene([root, child]), scene([{ ...root, revision: 2, x: 100 }, { ...child, parent_id: null, x: 500 }]), 'p').map(n => n.id)).toEqual(['r', 'a']));
  it('selects endpoints after undoing an association', () => expect(historyTargets(scene([root, { ...child, parent_id: null }], [{ id: 'edge', container_id: 'p', source_object_id: 'r', target_object_id: 'a', relation_type: 'reference' }]), scene([root, { ...child, parent_id: null }]), 'p').map(n => n.id)).toEqual(['r', 'a']));
  it('does not invent a target in a hidden or empty container', () => expect(historyTargets(scene([root]), scene([]), 'p')).toEqual([]));
});

it('reveals both old and new endpoints after same-ID reconnect, including a child of the other endpoint',()=>{
  const edge={id:'edge',container_id:'p',source_object_id:'r',target_object_id:'a',relation_type:'related'};
  const changed={...edge,target_object_id:'b'};
  expect(historyTargets(scene([root,child,grandchild],[edge]),scene([root,child,grandchild],[changed]),'p').map(n=>n.id)).toEqual(['r','a','b']);
});

it('focuses the node whose notes, labels or task changed without using its parent as a fallback',()=>{
 expect(historyTargets(scene([root,child]),scene([root,{...child,canvas_data:{note:'更改备注'}}]),'p').map(n=>n.id)).toEqual(['a']);
});

it('returns focus to the summarized branches when undo removes an independent summary topic',()=>{
 const summary={...root,id:'summary',canvas_data:{summary:{members:['a'],title:'概要'}}};
 expect(historyTargets(scene([root,child,summary]),scene([root,child]),'p').map(n=>n.id)).toEqual(['a']);
});
