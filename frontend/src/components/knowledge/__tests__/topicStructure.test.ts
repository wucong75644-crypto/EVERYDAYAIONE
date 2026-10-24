import { describe, it, expect } from "vitest";
import {
  arrangeStructure,
  structureRoot,
  structureMoves,
  topicStructures,
  effectiveStructure,
} from "../topicStructure";
import type { KnowledgeNode } from "../../../services/knowledge";
const node = (
  id: string,
  parent_id: string | null,
  x = 0,
  y = 0,
): KnowledgeNode => ({
  id,
  node_id: id,
  container_id: "p",
  parent_id,
  x,
  y,
  kind: "topic",
  title: id,
  revision: 1,
  layout_revision: 3,
  sort_order: Number(id.replace(/\D/g, "")) || 0,
});
const base = [
  node("root", null, 600, 400),
  node("a1", "root"),
  node("a2", "root"),
  node("a3", "root"),
  node("a4", "root"),
  node("b1", "a1"),
  node("b2", "a1"),
  node("other", null, 9999, 9999),
];
describe("topic structures", () => {
  it.each(topicStructures)(
    "lays out measured branches without overlap: $title",
    ({ id }) => {
      const input = base.map((n) =>
        n.id === "root" ? { ...n, canvas_data: { structure: id } } : n,
      );
      const sizes = new Map(
        input.map((n) => [
          n.id,
          {
            width: n.id === "a1" ? 400 : 272,
            height: n.id === "a2" ? 300 : 170,
          },
        ]),
      );
      const out = arrangeStructure(input, "root", sizes);
      expect(out[0]).toMatchObject({ x: 600, y: 400 });
      expect(out.at(-1)).toEqual(base.at(-1));
      expect(out.map((n) => n.parent_id)).toEqual(base.map((n) => n.parent_id));
      expect(input[1].x).toBe(0);
      for (let i = 0; i < out.length; i++)
        for (let j = i + 1; j < out.length; j++) {
          const a = out[i],
            b = out[j],
            sa = sizes.get(a.id)!,
            sb = sizes.get(b.id)!;
          expect(
            a.x + sa.width <= b.x ||
              b.x + sb.width <= a.x ||
              a.y + sa.height <= b.y ||
              b.y + sb.height <= a.y,
          ).toBe(true);
        }
      const children = out.filter((n) => n.parent_id === "root");
      if (id === "right") expect(children.every((n) => n.x > 600)).toBe(true);
      if (id === "left") expect(children.every((n) => n.x < 600)).toBe(true);
      if (id === "down") expect(children.every((n) => n.y > 400)).toBe(true);
      if (id === "up") expect(children.every((n) => n.y < 400)).toBe(true);
      if (id === "balanced") {
        expect(children.some((n) => n.x < 600)).toBe(true);
        expect(children.some((n) => n.x > 600)).toBe(true);
      }
    },
  );
  it("supports an explicit nested type while retaining the outer root anchor", () => {
    const input = base.map((n) => ({
      ...n,
      canvas_data:
        n.id === "root"
          ? { structure: "down" as const }
          : n.id === "a1"
            ? { structure: "left" as const }
            : {},
    }));
    expect(structureRoot(input, "b1")?.id).toBe("root");
    const out = arrangeStructure(input, "root", new Map());
    expect(out.find((n) => n.id === "b1")!.x).toBeLessThan(
      out.find((n) => n.id === "a1")!.x,
    );
    expect(out[0]).toMatchObject({ x: 600, y: 400 });
  });
  it("marks the inherited direction for nested nodes in a balanced structure", () => {
    const input = base.map((n) =>
      n.id === "root"
        ? { ...n, canvas_data: { structure: "balanced" as const } }
        : n,
    );
    expect(effectiveStructure(input, "a1")).toBe("right");
    expect(effectiveStructure(input, "a2")).toBe("left");
    expect(effectiveStructure(input, "b1")).toBe("right");
    expect(effectiveStructure(input, "root")).toBe("balanced");
  });
  it("increments moved metadata owners only once in revision expectations", () => {
    const after = base.map((n) => ({ ...n, x: n.x + 10 }));
    expect(
      structureMoves(base, after, new Set(["root"]))[0].expected_revision,
    ).toBe(4);
    expect(structureMoves(base, after)[0].expected_revision).toBe(3);
  });
});
