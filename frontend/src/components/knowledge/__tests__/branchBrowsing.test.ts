import { describe, it, expect } from "vitest";
import {
  branchFoldState,
  visibleSelection,
  centerTopic,
  centerTopicView,
} from "../branchBrowsing";
import type { KnowledgeNode } from "../../../services/knowledge";
const n = (id: string, parent_id: string | null, order = 1): KnowledgeNode => ({
  id,
  node_id: id,
  container_id: "p",
  parent_id,
  kind: "topic",
  title: id,
  revision: 1,
  layout_revision: 1,
  x: 20,
  y: 30,
  sort_order: order,
});
const nodes = [
  n("root", null),
  n("a", "root"),
  n("b", "a"),
  n("c", "b"),
  n("peer", "root", 2),
  n("other", null, 2),
];
describe("recursive branch browsing", () => {
  it("folds every parent in a branch and expands hidden grandchildren too", () => {
    const folded = branchFoldState(
      nodes,
      new Set(["unrelated"]),
      ["root"],
      false,
    );
    expect([...folded].sort()).toEqual(["a", "b", "root", "unrelated"]);
    expect([...branchFoldState(nodes, folded, ["root"], true)]).toEqual([
      "unrelated",
    ]);
  });
  it("does not unfold siblings or other roots", () => {
    const old = new Set(["root", "a", "b", "other"]);
    expect([...branchFoldState(nodes, old, ["a"], true)].sort()).toEqual([
      "other",
      "root",
    ]);
    expect(old.has("a")).toBe(true);
  });
  it("handles overlapping selections, missing ids and cycles without recursion", () => {
    const cycle = [n("a", "b"), n("b", "a")];
    expect(
      [
        ...branchFoldState(cycle, new Set(), ["a", "b", "missing"], false),
      ].sort(),
    ).toEqual(["a", "b"]);
  });
  it("maps hidden selections to their outermost visible folded ancestor", () => {
    expect(visibleSelection(nodes, new Set(["a", "b"]), "c")).toBe("a");
    expect(visibleSelection(nodes, new Set(["root", "a"]), "c")).toBe("root");
    expect(visibleSelection(nodes, new Set(["a"]), "peer")).toBe("peer");
    expect(visibleSelection(nodes, new Set(), "gone")).toBeNull();
  });
  it("does not cross space boundaries even with malformed parent links", () => {
    const mixed = [
      ...nodes,
      { ...n("foreign", "a"), container_id: "q" },
      { ...n("foreign-child", "foreign"), container_id: "q" },
    ];
    expect(
      branchFoldState(mixed, new Set(), ["root"], false).has("foreign"),
    ).toBe(false);
  });
  it("finds the selected tree root and falls back to an ordinary root in the current space", () => {
    expect(centerTopic(nodes, "c", "p")?.id).toBe("root");
    expect(centerTopic(nodes, "other", "p")?.id).toBe("other");
    expect(centerTopic(nodes, null, "q")).toBeUndefined();
    expect(centerTopic(nodes, "gone", "p")?.id).toBe("root");
  });
  it("returns from summary topics to the summarized tree", () => {
    const summary = {
      ...n("summary", null, 0),
      canvas_data: { summary: { title: "概要", members: ["a", "peer"] } },
    };
    expect(centerTopic([...nodes, summary], "summary", "p")?.id).toBe("root");
    expect(centerTopic([...nodes, summary], null, "p")?.id).toBe("root");
  });
  it("handles broken ancestors and cycles deterministically", () => {
    expect(
      centerTopic([...nodes, n("broken", "gone")], "broken", "p")?.id,
    ).toBe("root");
    expect(centerTopic([n("a", "b"), n("b", "a")], "a", "p")).toBeUndefined();
  });
  it("centers the actual node dimensions while preserving zoom", () => {
    expect(
      centerTopicView(
        { x: 999, y: 999, z: 0.5 },
        { x: 200, y: 400 },
        { width: 400, height: 200 },
        { width: 1000, height: 600 },
      ),
    ).toEqual({ x: 300, y: 50, z: 0.5 });
  });
});
