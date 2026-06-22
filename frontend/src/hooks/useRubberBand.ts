/**
 * 鼠标拖拽框选 Hook（rubber-band selection）
 *
 * 行业惯例（Finder / Explorer / iCloud Drive）：在空白处按住鼠标拖动，
 * 出现半透明矩形，与文件卡片相交的全部选中。
 *
 * 使用方式：
 *   const ref = useRef<HTMLDivElement>(null);
 *   const { rect } = useRubberBand({ containerRef: ref, onSelectionChange, enabled });
 *   <div ref={ref}> {rect && <div style={...rect}/>} </div>
 *
 * 集成约定：被框选的目标 DOM 元素必须带 `data-workspace-path="..."` 属性。
 * 启动阈值 = 5px（避免与"点击空白清空选中"冲突）。
 */

import { useEffect, useRef, useState } from 'react';

const DRAG_THRESHOLD_PX = 5;

interface Rect {
  left: number;
  top: number;
  width: number;
  height: number;
}

interface UseRubberBandOptions {
  /** 框选容器（事件 + 坐标基准） */
  containerRef: React.RefObject<HTMLElement | null>;
  /** 提交选中的路径列表（每次拖动结束触发一次） */
  onSelectionChange: (paths: string[], additive: boolean) => void;
  /** 是否启用（多选模式下应禁用，避免与复选框冲突）*/
  enabled: boolean;
}

interface UseRubberBandReturn {
  /** 当前拖拽矩形（相对容器；null = 未拖拽）*/
  rect: Rect | null;
  /** 是否处于拖拽中（用于阻断子元素点击事件冒泡的判断）*/
  isDragging: boolean;
}

/** 两矩形是否相交 */
function intersects(a: Rect, b: Rect): boolean {
  return !(
    a.left + a.width < b.left ||
    b.left + b.width < a.left ||
    a.top + a.height < b.top ||
    b.top + b.height < a.top
  );
}

export function useRubberBand({
  containerRef,
  onSelectionChange,
  enabled,
}: UseRubberBandOptions): UseRubberBandReturn {
  const [rect, setRect] = useState<Rect | null>(null);
  const startRef = useRef<{ x: number; y: number; additive: boolean } | null>(null);
  const draggingRef = useRef(false);

  // 容器内 mousedown：只在点击空白（target === container）时启动框选
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !enabled) return;

    const handleMouseDown = (e: MouseEvent) => {
      // 仅左键
      if (e.button !== 0) return;
      // 点击落在某个 [data-workspace-path] 元素内 → 不启动框选
      const target = e.target as HTMLElement;
      if (target.closest('[data-workspace-path]')) return;
      // 点击落在按钮/输入框等交互元素 → 不启动
      if (target.closest('button, input, textarea, [role="menuitem"], [role="tab"]')) return;

      const containerRect = container.getBoundingClientRect();
      startRef.current = {
        x: e.clientX - containerRect.left + container.scrollLeft,
        y: e.clientY - containerRect.top + container.scrollTop,
        additive: e.ctrlKey || e.metaKey || e.shiftKey,
      };
      draggingRef.current = false;
    };

    container.addEventListener('mousedown', handleMouseDown);
    return () => container.removeEventListener('mousedown', handleMouseDown);
  }, [containerRef, enabled]);

  // 全局 mousemove/mouseup（覆盖 mouse 拖出容器的情况）
  useEffect(() => {
    if (!enabled) return;

    const computeCurrent = (e: MouseEvent): { x: number; y: number } | null => {
      const container = containerRef.current;
      if (!container) return null;
      const cr = container.getBoundingClientRect();
      return {
        x: e.clientX - cr.left + container.scrollLeft,
        y: e.clientY - cr.top + container.scrollTop,
      };
    };

    const handleMove = (e: MouseEvent) => {
      const start = startRef.current;
      if (!start) return;
      const cur = computeCurrent(e);
      if (!cur) return;

      const dx = cur.x - start.x;
      const dy = cur.y - start.y;

      if (!draggingRef.current) {
        if (Math.abs(dx) < DRAG_THRESHOLD_PX && Math.abs(dy) < DRAG_THRESHOLD_PX) return;
        draggingRef.current = true;
      }

      const next: Rect = {
        left: Math.min(start.x, cur.x),
        top: Math.min(start.y, cur.y),
        width: Math.abs(dx),
        height: Math.abs(dy),
      };
      setRect(next);
    };

    const handleUp = () => {
      const start = startRef.current;
      if (!start) return;
      const wasDragging = draggingRef.current;
      const currentRect = rect;

      // 重置
      startRef.current = null;
      draggingRef.current = false;
      setRect(null);

      if (!wasDragging || !currentRect) return;

      // 容器内查找命中的项
      const container = containerRef.current;
      if (!container) return;
      const containerRect = container.getBoundingClientRect();
      const hits: string[] = [];
      container.querySelectorAll<HTMLElement>('[data-workspace-path]').forEach((el) => {
        const r = el.getBoundingClientRect();
        const elRect: Rect = {
          left: r.left - containerRect.left + container.scrollLeft,
          top: r.top - containerRect.top + container.scrollTop,
          width: r.width,
          height: r.height,
        };
        if (intersects(elRect, currentRect)) {
          const p = el.getAttribute('data-workspace-path');
          if (p) hits.push(p);
        }
      });

      onSelectionChange(hits, start.additive);
    };

    window.addEventListener('mousemove', handleMove);
    window.addEventListener('mouseup', handleUp);
    return () => {
      window.removeEventListener('mousemove', handleMove);
      window.removeEventListener('mouseup', handleUp);
    };
  }, [containerRef, enabled, rect, onSelectionChange]);

  // 拖拽中临时禁用文本选择，避免高亮文字
  useEffect(() => {
    if (rect) {
      document.body.style.userSelect = 'none';
      return () => { document.body.style.userSelect = ''; };
    }
  }, [rect]);

  return {
    rect,
    isDragging: rect !== null,
  };
}

/** 渲染拖拽矩形的内联样式辅助 */
export function rubberBandStyle(rect: Rect): React.CSSProperties {
  return {
    position: 'absolute',
    left: rect.left,
    top: rect.top,
    width: rect.width,
    height: rect.height,
    background: 'var(--s-accent-soft, rgba(59, 130, 246, 0.15))',
    border: '1px solid var(--s-accent, #3b82f6)',
    pointerEvents: 'none',
    zIndex: 5,
  };
}

export type { Rect };
