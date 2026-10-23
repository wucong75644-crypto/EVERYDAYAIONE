import { describe, expect, it } from 'vitest';
import { navigateNode, revealNode } from '../canvasNavigation';
import type { KnowledgeNode } from '../../../services/knowledge';
const make = (id: string, parent: string | null, order: number): KnowledgeNode => ({ id, parent_id: parent, node_id: id, container_id: 'p', title: id, kind: 'topic', revision: 1, layout_revision: 1, x: 0, y: 0, sort_order: order });
const nodes = [make('root',null,1),make('a','root',1),make('grand','a',1),make('b','root',2)];
describe('canvas keyboard navigation', () => {
  it('uses sibling order and stops at boundaries without entering descendants', () => {
    expect(navigateNode(nodes,'a','ArrowDown',new Set()).id).toBe('b');
    expect(navigateNode(nodes,'b','ArrowUp',new Set()).id).toBe('a');
    expect(navigateNode(nodes,'b','ArrowDown',new Set()).id).toBe('b');
  });
  it('collapses before moving to parent and expands before entering child', () => {
    expect(navigateNode(nodes,'a','ArrowLeft',new Set())).toEqual({ id:'a',collapse:'a' });
    expect(navigateNode(nodes,'a','ArrowLeft',new Set(['a'])).id).toBe('root');
    expect(navigateNode(nodes,'a','ArrowRight',new Set(['a']))).toEqual({ id:'a',expand:'a' });
    expect(navigateNode(nodes,'a','ArrowRight',new Set()).id).toBe('grand');
    expect(navigateNode(nodes,'grand','ArrowRight',new Set()).id).toBe('grand');
  });
  it('starts at a root in the active space', () => {
    expect(navigateNode(nodes,null,'ArrowDown',new Set(),'other').id).toBeNull();
    expect(navigateNode(nodes,null,'ArrowDown',new Set(),'p').id).toBe('root');
  });
  it('keeps zoom and visible cards fixed, with minimal pan for offscreen cards', () => {
    const view={x:60,y:95,z:1.5},box={width:900,height:600},card={width:240,height:120};
    expect(revealNode(view,{x:40,y:40},card,box)).toEqual(view);
    const next=revealNode(view,{x:1000,y:700},card,box);
    expect(next).toEqual({x:-1000,y:-694,z:1.5});
    expect(revealNode(next,{x:1000,y:700},card,box)).toEqual(next);
  });
  it('anchors oversized cards at a stable leading edge', () => {
    const next=revealNode({x:0,y:0,z:2},{x:-100,y:-100},{width:1000,height:1000},{width:800,height:600});
    expect(next).toEqual({x:232,y:264,z:2});
  });
});
