import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
} from "@testing-library/react";
import { beforeEach, afterEach, it, expect, vi } from "vitest";
import { CanvasRangeHandles } from "../CanvasRangeHandles";
import type { KnowledgeNode } from "../../../services/knowledge";
const a: KnowledgeNode = {
  id: "a",
  node_id: "la",
  container_id: "c",
  parent_id: "root",
  title: "A",
  kind: "topic",
  revision: 1,
  layout_revision: 1,
  x: 0,
  y: 0,
  sort_order: 1,
};
const b = { ...a, id: "b", y: 200, sort_order: 2 },
  c = { ...a, id: "c", y: 400, sort_order: 3 };
const group = {
  ...a,
  id: "sum",
  title: "概要",
  parent_id: null,
  canvas_data: { summary: { members: ["a", "b"], title: "概要" } },
};
beforeEach(() => {
  vi.stubGlobal(
    "PointerEvent",
    class extends MouseEvent {
      pointerId: number;
      constructor(t: string, o: PointerEventInit = {}) {
        super(t, o);
        this.pointerId = o.pointerId ?? 1;
      }
    },
  );
  HTMLElement.prototype.setPointerCapture = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("previews a new range and commits only once after release", async () => {
  const save = vi.fn().mockResolvedValue(true),
    preview = vi.fn();
  render(
    <div className="kc-world">
      <CanvasRangeHandles
        node={group}
        nodes={[a, b, c, group]}
        kind="summary"
        sizes={new Map()}
        zoom={1}
        onSave={save}
        onPreview={preview}
      />
    </div>,
  );
  const end = screen.getByLabelText("调整概要结束范围 概要");
  fireEvent.pointerDown(end, { button: 0, pointerId: 1 });
  fireEvent.pointerMove(end, { clientY: 546, pointerId: 1 });
  expect(preview).toHaveBeenLastCalledWith(["a", "b", "c"]);
  expect(save).not.toHaveBeenCalled();
  fireEvent.pointerUp(end, { pointerId: 1 });
  await waitFor(() => expect(save).toHaveBeenCalledWith(["a", "b", "c"]));
  expect(save).toHaveBeenCalledTimes(1);
});
it("Esc cancels preview without saving", () => {
  const save = vi.fn(),
    preview = vi.fn();
  render(
    <div className="kc-world">
      <CanvasRangeHandles
        node={group}
        nodes={[a, b, c, group]}
        kind="summary"
        sizes={new Map()}
        zoom={1}
        onSave={save}
        onPreview={preview}
      />
    </div>,
  );
  const end = screen.getByLabelText("调整概要结束范围 概要");
  fireEvent.pointerDown(end, { button: 0, pointerId: 1 });
  fireEvent.pointerMove(end, { clientY: 546, pointerId: 1 });
  fireEvent.keyDown(end, { key: "Escape" });
  fireEvent.pointerUp(end, { pointerId: 1 });
  expect(save).not.toHaveBeenCalled();
  expect(preview).toHaveBeenLastCalledWith(null);
});
it("supports keyboard boundary adjustment without selecting other parents", async () => {
  const save = vi.fn().mockResolvedValue(true);
  render(
    <CanvasRangeHandles
      node={{
        ...a,
        canvas_data: { boundary: { title: "外框", members: ["a", "b"] } },
      }}
      nodes={[a, b, c, { ...c, id: "foreign", parent_id: "other" }]}
      kind="boundary"
      sizes={new Map()}
      zoom={1}
      onSave={save}
      onPreview={vi.fn()}
    />,
  );
  fireEvent.keyDown(screen.getByLabelText("调整外框结束范围 A"), {
    key: "ArrowDown",
  });
  await waitFor(() => expect(save).toHaveBeenCalledWith(["a", "b", "c"]));
});
