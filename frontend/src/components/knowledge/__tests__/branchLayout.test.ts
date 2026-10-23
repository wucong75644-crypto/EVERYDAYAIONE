import { describe, expect, it } from 'vitest';
import { branchIds, planBranch, planBranches, siblingCompare } from '../branchLayout';
import type { KnowledgeNode } from '../../../services/knowledge';
const make = (id: string, parent: string | null, x: number, y: number, order = 1): KnowledgeNode => ({ id, parent_id: parent, node_id: id, container_id: 'p', kind: 'topic', title: id, x, y, revision: 1, layout_revision: 1, sort_order: order });
const nodes = [make('root', null, 0, 0), make('a', 'root', 330, 0), make('grand', 'a', 660, 0), make('b', 'root', 330, 300, 2), make('c', 'root', 330, 600, 3)];
const sizes = new Map(nodes.map(n => [n.id, { width: 240, height: 120 }]));
const visible = new Set(nodes.map(n => n.id));
describe('branch drop layout', () => {
  it('moves the complete branch including hidden descendants, retaining offsets and hierarchy', () => {
    const plan = planBranch(nodes, 'a', { x: 80, y: 90 }, { x: 1000, y: 800 }, sizes, new Set(['a','b','root']));
    expect(plan.valid).toBe(true); expect(plan.placement).toBe('free'); expect([...plan.moving]).toEqual(['a','grand']);
    expect(plan.nodes.find(n => n.id === 'grand')).toMatchObject({ x: 740, y: 90, parent_id: 'a' });
    expect(plan.nodes.find(n => n.id === 'b')).toEqual(nodes[3]); expect(plan.changed).toHaveLength(2);
  });
  it('previews a new parent and retains the descendant link', () => {
    const plan = planBranch(nodes, 'a', { x: 0, y: 0 }, { x: 400, y: 360 }, sizes, visible);
    expect(plan).toMatchObject({ target: 'b', placement: 'child', valid: true });
    expect(plan.nodes.find(n => n.id === 'a')?.parent_id).toBe('b'); expect(plan.nodes.find(n => n.id === 'grand')?.parent_id).toBe('a');
  });
  it('inserts before with a marker and shifts complete sibling trees to avoid collisions', () => {
    const plan = planBranch(nodes, 'c', { x: 0, y: -600 }, { x: 400, y: 5 }, sizes, visible);
    expect(plan).toMatchObject({ target: 'a', placement: 'before', valid: true, marker: { x: 322, y: -12, width: 256 } });
    expect(plan.nodes.filter(n => n.parent_id === 'root').sort(siblingCompare).map(n => n.id)).toEqual(['c','a','b']);
    const a = plan.nodes.find(n => n.id === 'a')!, grand = plan.nodes.find(n => n.id === 'grand')!;
    expect(a.y).toBeGreaterThanOrEqual(152); expect(grand.y).toBe(a.y); expect(grand.x - a.x).toBe(330);
  });
  it('inserts after the target with a distinct lower marker', () => {
    const plan = planBranch(nodes, 'a', { x: 0, y: 400 }, { x: 400, y: 410 }, sizes, visible);
    expect(plan.placement).toBe('after'); expect(plan.marker?.y).toBe(432);
    expect(plan.nodes.filter(n => n.parent_id === 'root').sort(siblingCompare).map(n => n.id)).toEqual(['b','a','c']);
  });
  it('refuses descendants and cross-space destinations and never targets folded nodes', () => {
    expect(planBranch(nodes, 'a', { x: 0, y: 0 }, { x: 700, y: 60 }, sizes, visible).valid).toBe(false);
    const other = nodes.map(n => n.id === 'b' ? { ...n, container_id: 'other' } : n);
    expect(planBranch(other, 'a', { x: 0, y: 0 }, { x: 400, y: 360 }, sizes, visible).valid).toBe(false);
    expect(planBranch(nodes, 'a', { x: 0, y: 0 }, { x: 400, y: 360 }, sizes, new Set(['root','a'])).placement).toBe('free');
  });
  it('terminates descendant enumeration even with malformed cyclic input', () => {
    expect(branchIds([make('x','y',0,0),make('y','x',0,0)],'x').size).toBe(2);
  });
});

describe('selection branch planning', () => {
  it('moves the union exactly once and includes folded descendants', () => {
    const plan = planBranches(nodes, ['a', 'grand', 'b'], { x: 70, y: 80 }, { x: 1200, y: 900 }, sizes, new Set(['root','a','b','c']));
    expect(plan.moving.size).toBe(3); expect(plan.changed).toHaveLength(3);
    expect(plan.nodes.find(n => n.id === 'grand')).toMatchObject({ x: 730, y: 80, parent_id: 'a' });
    expect(plan.nodes.find(n => n.id === 'b')).toMatchObject({ x: 400, y: 380, parent_id: 'root' });
  });
  it.each([{ y: 605, placement: 'before' }, { y: 660, placement: 'child' }, { y: 715, placement: 'after' }])('shows group $placement and moves each selected root together', ({ y, placement }) => {
    const plan = planBranches(nodes, ['b','a','grand'], { x: 20, y: 40 }, { x: 400, y }, sizes, visible);
    expect(plan).toMatchObject({ target: 'c', placement, valid: true });
    if (placement !== 'child') expect(plan.marker).toBeDefined();
    const a = plan.nodes.find(n => n.id === 'a')!, b = plan.nodes.find(n => n.id === 'b')!, grand = plan.nodes.find(n => n.id === 'grand')!;
    expect(a.parent_id).toBe(placement === 'child' ? 'c' : 'root'); expect(b.parent_id).toBe(a.parent_id);
    expect(grand.parent_id).toBe('a'); expect(grand.x - a.x).toBe(330); expect(grand.y).toBe(a.y);
    expect(a.sort_order).toBeLessThan(b.sort_order);
  });
  it('rejects any moving descendant as a target', () => {
    expect(planBranches(nodes, ['a','b'], { x: 0, y: 0 }, { x: 700, y: 60 }, sizes, visible).valid).toBe(false);
  });
});

it('propagates deep subtree growth through all ancestors without cross-branch overlap', () => {
  const tree = [make('root',null,0,0),make('a','root',330,0),make('aa','a',660,0),make('aaa','aa',990,0),make('b','root',330,300,2),make('bb','b',660,300),make('bbb','bb',990,300),make('c','root',330,600,3)];
  const plan = planBranch(tree,'c',{x:0,y:0},{x:700,y:85},new Map(tree.map(n=>[n.id,{width:272,height:170}])),new Set(tree.map(n=>n.id)));
  const result = new Map(plan.nodes.map(n=>[n.id,n]));
  const aTree = plan.nodes.filter(n=>branchIds(plan.nodes,'a').has(n.id));
  const bTree = plan.nodes.filter(n=>branchIds(plan.nodes,'b').has(n.id));
  expect(Math.min(...bTree.map(n=>n.y))).toBeGreaterThanOrEqual(Math.max(...aTree.map(n=>n.y+170))+32);
  expect(result.get('bbb')!.y-result.get('b')!.y).toBe(0);
  expect(plan.changed.some(n=>n.id==='bbb')).toBe(true);
  expect(tree.find(n=>n.id==='b')!.y).toBe(300);
});
