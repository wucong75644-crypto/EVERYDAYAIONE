import { branchDirection, vertical } from "./structureDirection";
import { rangePoint } from "./groupGeometry";
import { useRef, useState } from "react";
import type { KnowledgeNode } from "../../services/knowledge";
import { groupBounds } from "./canvasTools";
import { siblingCompare } from "./branchLayout";
type GroupKind = "summary" | "boundary";
export function CanvasRangeHandles({
  node,
  nodes,
  kind,
  sizes,
  zoom,
  onSave,
  onPreview,
  frameInset=0,
}: {
  node: KnowledgeNode;
  nodes: KnowledgeNode[];
  kind: GroupKind;
  sizes: Map<string, { width: number; height: number }>;
  zoom: number;
  onSave: (members: string[]) => Promise<boolean>;
  onPreview: (members: string[] | null) => void;
  frameInset?:number;
}) {
  const group = node.canvas_data?.[kind];
  const direction = branchDirection(nodes, group?.members[0] ?? node.id),
    horizontalRange = vertical(direction);
  const first = nodes.find((n) => n.id === group?.members[0]);
  const peers = nodes
    .filter(
      (n) =>
        first &&
        n.parent_id === first.parent_id &&
        branchDirection(nodes, n.id) === direction &&
        !n.canvas_data?.summary,
    )
    .sort(siblingCompare);
  const [preview, setPreview] = useState<string[] | null>(null),
    [busy, setBusy] = useState(false);
  const drag = useRef<{
    edge: number;
    members: string[];
    changed: boolean;
  } | null>(null);
  if (!group) return null;
  const members = preview ?? group.members;
  const bounds = groupBounds(nodes, members, sizes);
  if (!bounds) return null;
  const range = (edge: number, index: number, base = members) => {
    const indices = base
      .map((id) => peers.findIndex((n) => n.id === id))
      .filter((i) => i >= 0)
      .sort((a, b) => a - b);
    if (!indices.length) return base;
    const fixed = edge === 0 ? indices.at(-1)! : indices[0],
      low = Math.min(index, fixed),
      high = Math.max(index, fixed);
    return peers.slice(low, high + 1).map((n) => n.id);
  };
  const cancel = () => {
    drag.current = null;
    setPreview(null);
    onPreview(null);
  };
  const save = async (next: string[]) => {
    setBusy(true);
    try {
      await onSave(next);
    } finally {
      setBusy(false);
      cancel();
    }
  };
  return (
    <>
      {[0, 1].map((edge) => (
        <button
          key={edge}
          className="kc-range-handle"
          disabled={busy}
          aria-label={
            "调整" +
            (kind === "summary" ? "概要" : "外框") +
            (edge === 0 ? "起始" : "结束") +
            "范围 " +
            node.title
          }
          title="拖动调整范围；方向键调整，Esc 取消"
          style={{
            left: rangePoint(bounds, direction, edge, kind).x - 6+(horizontalRange?(edge?frameInset:-frameInset):(direction==='right'?frameInset:-frameInset)),
            top: rangePoint(bounds, direction, edge, kind).y - 6+(horizontalRange?(direction==='down'?frameInset:-frameInset):(edge?frameInset:-frameInset)),
            cursor: horizontalRange ? "ew-resize" : "ns-resize",
          }}
          onPointerDown={(e) => {
            if (e.button !== 0) return;
            e.preventDefault();
            e.stopPropagation();
            e.currentTarget.focus({ preventScroll: true });
            drag.current = {
              edge,
              members: [...group.members],
              changed: false,
            };
            e.currentTarget.setPointerCapture(e.pointerId);
          }}
          onPointerMove={(e) => {
            e.stopPropagation();
            if (!drag.current) return;
            const world = e.currentTarget
                .closest(".kc-world")
                ?.getBoundingClientRect(),
              coordinate = horizontalRange
                ? (e.clientX - (world?.left ?? 0)) / zoom
                : (e.clientY - (world?.top ?? 0)) / zoom;
            let nearest = 0,
              distance = Infinity;
            peers.forEach((n, i) => {
              const b = groupBounds(nodes, [n.id], sizes);
              if (b) {
                const d = Math.abs(
                  coordinate -
                    (horizontalRange
                      ? edge === 0
                        ? b.x
                        : b.right
                      : edge === 0
                        ? b.y
                        : b.bottom),
                );
                if (d < distance) {
                  distance = d;
                  nearest = i;
                }
              }
            });
            const next = range(edge, nearest, drag.current.members);
            drag.current.changed =
              JSON.stringify(next) !== JSON.stringify(group.members);
            setPreview(next);
            onPreview(next);
          }}
          onPointerUp={(e) => {
            e.stopPropagation();
            if (drag.current?.changed && preview) {
              drag.current = null;
              void save(preview);
            } else cancel();
          }}
          onPointerCancel={(e) => {
            e.stopPropagation();
            cancel();
          }}
          onLostPointerCapture={(e) => {
            e.stopPropagation();
            if (drag.current) cancel();
          }}
          onKeyDown={(e) => {
            e.stopPropagation();
            if (e.key === "Escape") {
              e.preventDefault();
              cancel();
            }
            if (
              (horizontalRange
                ? ["ArrowLeft", "ArrowRight"]
                : ["ArrowUp", "ArrowDown"]
              ).includes(e.key)
            ) {
              e.preventDefault();
              const indices = members
                  .map((id) => peers.findIndex((n) => n.id === id))
                  .sort((a, b) => a - b),
                current = edge === 0 ? indices[0] : indices.at(-1)!;
              const next = range(
                edge,
                Math.max(
                  0,
                  Math.min(
                    peers.length - 1,
                    current +
                      (["ArrowUp", "ArrowLeft"].includes(e.key) ? -1 : 1),
                  ),
                ),
              );
              void save(next);
            }
          }}
        >
          {" "}
        </button>
      ))}
    </>
  );
}
