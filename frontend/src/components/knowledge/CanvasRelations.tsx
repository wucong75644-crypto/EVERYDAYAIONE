import { useLayoutEffect, useState } from 'react';
import type { PointerEvent, RefObject } from 'react';
import type { KnowledgeNode, KnowledgeRelation } from '../../services/knowledge';
import { relationCurve, relationLane, relationTypes } from './relationGeometry';
type Props = {
  nodes: KnowledgeNode[]; relations: KnowledgeRelation[]; viewport: RefObject<HTMLDivElement | null>;
  selected: string | null; writable: boolean; disabled: boolean; zoom: number; dragging: string | null;
  entering?: Set<string>; measurements?: Record<string, { width: number; height: number }>;
  onSelect: (id: string) => void;
  onReconnect: (relation: KnowledgeRelation, endpoint: 'source' | 'target', event: PointerEvent<SVGCircleElement>) => void;
};
export function CanvasRelations({ nodes, relations, viewport, selected, writable, disabled, zoom, dragging, entering, measurements, onSelect, onReconnect }: Props) {
  const [sizes, setSizes] = useState<Record<string, { width: number; height: number }>>({});
  const nodeIdentity = nodes.map(n => n.id).join(',');
  useLayoutEffect(() => {
    const elements = [...(viewport.current?.querySelectorAll<HTMLElement>('[data-node-id]') ?? [])];
    const measure = () => {
      const next = Object.fromEntries(elements.map(el => [el.dataset.nodeId!, { width: el.offsetWidth || 272, height: el.offsetHeight || 170 }]));
      setSizes(old => JSON.stringify(old) === JSON.stringify(next) ? old : next);
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure); elements.forEach(el => observer.observe(el));
    return () => observer.disconnect();
  }, [nodeIdentity, viewport]);
  const index = new Map(nodes.map(n => [n.id, n]));
  return <svg className="kc-edges kc-associations" aria-label="关联线">
    {relations.map(r => {
      const a = index.get(r.source_object_id), b = index.get(r.target_object_id); if (!a || !b) return null;
      const lane = relationLane(relations, r, zoom);
      const curve = relationCurve({ ...a, ...(measurements?.[a.id] ?? sizes[a.id] ?? { width: 272, height: 170 }) }, { ...b, ...(measurements?.[b.id] ?? sizes[b.id] ?? { width: 272, height: 170 }) }, lane.offset);
      const active = selected === r.id;
      return <g key={r.id} data-relation-id={r.id} className={'kc-relation' + (entering?.has(r.source_object_id) || entering?.has(r.target_object_id) ? ' kc-edge-enter' : '') + (active ? ' selected' : '') + (dragging === r.id ? ' dragging' : '')}>
        <path className="kc-relation-hit" d={curve.path} role="button" aria-label={`关联线：${a.title} → ${b.title}${lane.count > 1 ? `（${relationTypes[r.relation_type] ?? '关联'}）` : ''}`} aria-pressed={active} tabIndex={disabled ? -1 : 0}
          onPointerDown={e => { e.stopPropagation(); if (e.button === 0 && !disabled) { e.preventDefault(); onSelect(r.id); } }}
          onClick={e => { e.stopPropagation(); if (!disabled) onSelect(r.id); }}
          onDoubleClick={e => e.stopPropagation()} onContextMenu={e => { e.preventDefault(); e.stopPropagation(); if (!disabled) onSelect(r.id); }}
          onKeyDown={e => { if (!disabled && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); e.stopPropagation(); onSelect(r.id); } }} />
        <path className="association" d={curve.path} aria-hidden="true" />
        {active && writable && !disabled && (['source', 'target'] as const).map(endpoint => {
          const port = endpoint === 'source' ? curve.start : curve.end;
          return <circle key={endpoint} className="kc-relation-endpoint" cx={port.x + port.nx * 9 / zoom} cy={port.y + port.ny * 9 / zoom} r={7 / zoom} aria-label={`拖动关联线${endpoint === 'source' ? '起点' : '终点'}重新连接`}
            onPointerDown={e => { e.stopPropagation(); e.preventDefault(); if (e.button === 0) onReconnect(r, endpoint, e); }} onClick={e => e.stopPropagation()} onDoubleClick={e => e.stopPropagation()} onContextMenu={e => { e.stopPropagation(); e.preventDefault(); }} />;
        })}
      </g>;
    })}
  </svg>;
}
