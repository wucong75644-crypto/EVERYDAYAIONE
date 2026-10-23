import { describe, expect, it } from 'vitest';
import { placeContent, reflowContent } from '../contentLayout';
import type { KnowledgeNode } from '../../../services/knowledge';
const node = (id: string, parent_id: string | null, x: number, y: number, order = y) => ({ id, node_id: id, container_id: 'p', parent_id, x, y, sort_order: order, title: id }) as KnowledgeNode;
describe('content layout', () => {
  it('moves complete later peer branches when a title grows', () => {
    const nodes = [node('root', null, 0, 0), node('a', 'root', 330, 0), node('b', 'root', 330, 200), node('bc', 'b', 660, 200), node('c', 'root', 330, 400)];
    const sizes = new Map(nodes.map(n => [n.id, { width: 240, height: n.id === 'a' ? 405 : 116 }]));
    const result = reflowContent(nodes, sizes), get = (id: string) => result.find(n => n.id === id)!;
    expect(get('a').y).toBe(0); expect(get('b').y).toBe(437); expect(get('bc').y).toBe(437); expect(get('c').y).toBe(585); expect(nodes[2].y).toBe(200);
  });
  it('propagates inner growth to ancestor peers', () => {
    const nodes = [node('a', null, 0, 0), node('a1', 'a', 330, 0), node('a2', 'a', 330, 200), node('b', null, 0, 500), node('bc', 'b', 330, 500)];
    const sizes = new Map(nodes.map(n => [n.id, { width: 240, height: n.id === 'a1' ? 500 : 116 }]));
    const result = reflowContent(nodes, sizes); expect(result.find(n => n.id === 'b')!.y).toBe(680); expect(result.find(n => n.id === 'bc')!.y).toBe(680);
  });
  it('reserves descendants, repeats avoidance and keeps unobstructed clicks exact', () => {
    const source = [node('a', null, 0, 0), node('ac', 'a', 330, 0)], occupied = [node('b', null, 660, 100), node('c', null, 330, 300)];
    const sizes = new Map([...source, ...occupied].map(n => [n.id, { width: 240, height: 116 }]));
    expect(placeContent(source, occupied, sizes, { x: 330, y: 100 })).toEqual({ x: 330, y: 440 });
    expect(placeContent(source, occupied, sizes, { x: 0, y: 800 })).toEqual({ x: 0, y: 800 });
  });
});
