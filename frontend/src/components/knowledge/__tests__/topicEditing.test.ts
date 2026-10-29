import { describe, it, expect } from "vitest";
import {
  titleCaretAt,
  titleHeight,
  editSelection,
  reorderTopic,
  removeTopic,
} from "../topicEditing";
import type { KnowledgeNode } from "../../../services/knowledge";
const node = (
  id: string,
  parent_id: string | null,
  order: number,
): KnowledgeNode => ({
  id,
  node_id: id,
  parent_id,
  sort_order: order,
  container_id: "p",
  kind: "topic",
  title: id,
  x: order * 350,
  y: order * 200,
  revision: 1,
  layout_revision: 1,
});
describe("topic editing", () => {
  it("measures explicit lines and narrow widths", () => {
    expect(titleHeight("第一行\n第二行\n第三行\n第四行")).toBeGreaterThan(
      titleHeight("第一行"),
    );
    expect(titleHeight("字".repeat(100), 160)).toBeGreaterThan(
      titleHeight("字".repeat(100), 800),
    );
  });
  it("appends to modified titles and selects default titles", () => {
    expect(editSelection("已有内容", false)).toEqual({ start: 4, end: 4 });
    expect(editSelection("新主题", false)).toEqual({ start: 0, end: 3 });
    expect(editSelection("已有内容", true)).toEqual({ start: 0, end: 4 });
  });
  it.each(["right", "left", "up", "down", "balanced"] as const)(
    "reorders one complete branch along %s",
    (structure) => {
      const root = { ...node("r", null, 0), canvas_data: { structure } },
        a = node("a", "r", 1),
        b = node("b", "r", 2),
        child = node("c", "b", 1),
        nodes = [root, a, b, child];
      const plan = reorderTopic(nodes, b, -1, new Map());
      expect(plan?.placement).toBe("before");
      expect(plan?.target).toBe("a");
      expect(plan?.nodes.find((n) => n.id === "c")?.parent_id).toBe("b");
      expect(reorderTopic(nodes, a, -1, new Map())).toBeNull();
    },
  );
  it.each(["right", "left", "up", "down", "balanced"] as const)(
    "promotes children in place without deleting descendants along %s",
    (structure) => {
      const r = { ...node("r", null, 0), canvas_data: { structure } },
        a = node("a", "r", 1),
        b = node("b", "r", 2),
        c = node("c", "a", 1),
        d = node("d", "a", 2),
        e = node("e", "c", 1);
      const original = [r, a, b, c, d, e],
        plan = removeTopic(original, a, new Map());
      expect(plan.nodes.map((n) => n.id)).not.toContain("a");
      expect(
        plan.nodes
          .filter((n) => n.parent_id === "r")
          .sort((a, b) => a.sort_order! - b.sort_order!)
          .map((n) => n.id),
      ).toEqual(["c", "d", "b"]);
      expect(plan.nodes.find((n) => n.id === "e")?.parent_id).toBe("c");
      expect(c.parent_id).toBe("a");
    },
  );
});

it('resolves the clicked text offset for multiline editing',()=>{const h=document.createElement('h3');h.textContent='第一行\n第二行';document.body.append(h);const doc=document as Document&{caretPositionFromPoint?:unknown};Object.defineProperty(doc,'caretPositionFromPoint',{configurable:true,value:()=>({offsetNode:h.firstChild,offset:5})});expect(titleCaretAt(h,0,0)).toBe(5);Object.defineProperty(doc,'caretPositionFromPoint',{configurable:true,value:undefined});h.remove();});

it('fits compact topics to their content while respecting manual widths, rich text and media',async()=>{
 const {topicWidth,topicSize}=await import('../topicEditing');const short=node('短',null,1),long={...short,title:'这是比较长的一行标题'};expect(topicWidth(short)).toBe(80);expect(topicSize(short).height).toBe(48);expect(topicWidth(long)).toBeGreaterThan(topicWidth(short));expect(topicWidth({...long,canvas_data:{width:500}})).toBe(500);expect(topicWidth({...long,canvas_data:{title_format:{align:'left',runs:[{text:long.title,font_size:32}]}}})).toBeGreaterThan(topicWidth(long));expect(topicWidth({...short,canvas_data:{image:{binding_id:'b',revision:1,width:240,placement:'top'}}})).toBeGreaterThanOrEqual(272);expect(topicSize({...short,canvas_data:{video:{binding_id:'v',revision:1}}}).height).toBeGreaterThan(topicSize(short).height);
});

it("keeps explicit height as a content-safe minimum",async()=>{const {topicSize}=await import('../topicEditing');const n=node('sized',null,1);expect(topicSize({...n,canvas_data:{width:360,height:300}})).toEqual({width:360,height:300});expect(topicSize({...n,canvas_data:{height:80}}).height).toBeGreaterThanOrEqual(48);});
