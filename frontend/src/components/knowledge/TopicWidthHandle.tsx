import { useRef } from "react";
import type { PointerEvent } from "react";
export function TopicWidthHandle({
  width,
  zoom,
  onPreview,
  onCommit,
  onCancel,
}: {
  width: number;
  zoom: number;
  onPreview: (width: number) => void;
  onCommit: (width: number|null) => void;
  onCancel: () => void;
}) {
  const drag = useRef<{
    x: number;
    width: number;
    value: number;
    pointer: number;
  } | null>(null);
  const cancel = () => {
    drag.current = null;
    onCancel();
  };
  const move = (e: PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    d.value = Math.max(
      80,
      Math.min(800, Math.round(d.width + (e.clientX - d.x) / zoom)),
    );
    onPreview(d.value);
  };
  return (
    <button
      className="kc-width-handle"
      aria-label="调整节点宽度"
      role="slider"
      aria-valuemin={80}
      aria-valuemax={800}
      aria-valuenow={width}
      title="拖动调整宽度；双击恢复适应内容；方向键微调；Esc 取消"
      onPointerDown={(e) => {
        e.stopPropagation();
        if (e.button !== 0) return;
        e.preventDefault();
        e.currentTarget.focus({ preventScroll: true });
        drag.current = {
          x: e.clientX,
          width,
          value: width,
          pointer: e.pointerId,
        };
        e.currentTarget.setPointerCapture(e.pointerId);
      }}
      onPointerMove={move}
      onPointerUp={(e) => {
        const d = drag.current;
        if (!d) return;
        move(e);
        drag.current = null;
        e.currentTarget.releasePointerCapture(e.pointerId);
        onCommit(d.value);
      }}
      onPointerCancel={cancel}
      onLostPointerCapture={() => {
        if (drag.current) cancel();
      }}
      onDoubleClick={(e) => {
        e.stopPropagation();
        onCommit(null);
      }}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === "Escape") {
          e.preventDefault();
          if (drag.current) {
            e.currentTarget.releasePointerCapture(drag.current.pointer);
            cancel();
          }
        } else if (["ArrowLeft", "ArrowRight", "Home"].includes(e.key)) {
          e.preventDefault();
          onCommit(
            e.key === "Home"
              ? null
              : Math.max(
                  80,
                  Math.min(800, width + (e.key === "ArrowRight" ? 10 : -10)),
                ),
          );
        }
      }}
    />
  );
}
