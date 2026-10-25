import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
} from "@testing-library/react";
import { afterEach, it, expect, vi } from "vitest";
import { CanvasVisualEditor } from "../CanvasVisualEditor";
import { nodeStyle, titleStyle, defaultStyle } from "../canvasStyle";
import type { KnowledgeNode } from "../../../services/knowledge";
vi.mock("../../../services/knowledge", () => ({
  knowledgeFilesApi: { list: vi.fn().mockResolvedValue([]) },
}));
const a: KnowledgeNode = {
  id: "a",
  node_id: "la",
  container_id: "c",
  parent_id: "r",
  title: "A",
  kind: "topic",
  revision: 1,
  layout_revision: 1,
  x: 0,
  y: 0,
};
const b = { ...a, id: "b", title: "B" },
  c = { ...a, id: "c", title: "C" },
  other = { ...a, id: "foreign", parent_id: "elsewhere", title: "其他分支" };
afterEach(cleanup);
const props = {
  node: a,
  nodes: [a, b, c, other],
  pending: false,
  unknown: false,
  onCheck: vi.fn(),
  onUploaded: vi.fn(),
  onSave: vi.fn().mockResolvedValue(true),
  onClose: vi.fn(),
};
it("edits boundary members without offering other parents and retains metadata", async () => {
  const save = vi.fn().mockResolvedValue(true);
  render(
    <CanvasVisualEditor
      {...props}
      node={{
        ...a,
        canvas_data: {
          note: "保留",
          boundary: { members: ["a", "b"], title: "范围" },
        },
      }}
      mode="boundary"
      onSave={save}
    />,
  );
  expect(screen.queryByText("其他分支")).toBeNull();
  fireEvent.click(screen.getByLabelText("C"));
  fireEvent.click(screen.getByText("保存修改"));
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith({
      note: "保留",
      boundary: { members: ["a", "b", "c"], title: "范围" },
    }),
  );
});
it("rejects an empty group", () => {
  const save = vi.fn();
  render(
    <CanvasVisualEditor
      {...props}
      node={{
        ...a,
        canvas_data: { boundary: { members: ["a"], title: "范围" } },
      }}
      mode="boundary"
      onSave={save}
    />,
  );
  fireEvent.click(screen.getByLabelText("A"));
  fireEvent.click(screen.getByText("保存修改"));
  expect(screen.getByRole("alert")).toHaveTextContent("至少保留一个分支");
  expect(save).not.toHaveBeenCalled();
});
it("edits summary range while excluding its independent summary topic", async () => {
  const save = vi.fn().mockResolvedValue(true);
  render(
    <CanvasVisualEditor
      {...props}
      node={{
        ...a,
        id: "sum",
        parent_id: null,
        canvas_data: { summary: { members: ["a", "b"], title: "概要" } },
      }}
      mode="summary"
      onSave={save}
    />,
  );
  fireEvent.click(screen.getByLabelText("C"));
  fireEvent.click(screen.getByText("保存修改"));
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith({
      summary: { members: ["a", "b", "c"], title: "概要" },
    }),
  );
});
it("previews and saves styles without touching annotations", async () => {
  const save = vi.fn().mockResolvedValue(true);
  render(
    <CanvasVisualEditor
      {...props}
      node={{ ...a, canvas_data: { note: "保留" } }}
      mode="style"
      onSave={save}
    />,
  );
  fireEvent.click(screen.getByLabelText("加粗"));
  fireEvent.change(screen.getByLabelText("文字颜色"), {
    target: { value: "#ff0000" },
  });
  fireEvent.click(screen.getByText("保存修改"));
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith({
      note: "保留",
      style: { ...defaultStyle, bold: true, color: "#ff0000" },
    }, {align:undefined,textMarks:["bold","color"]}),
  );
});
it("keeps edited data after save failure and exposes unknown result checking", async () => {
  const save = vi.fn().mockResolvedValue(false),
    close = vi.fn();
  const { rerender } = render(
    <CanvasVisualEditor
      {...props}
      mode="style"
      onSave={save}
      onClose={close}
    />,
  );
  fireEvent.click(screen.getByLabelText("斜体"));
  fireEvent.click(screen.getByText("保存修改"));
  await waitFor(() => expect(save).toHaveBeenCalled());
  expect(close).not.toHaveBeenCalled();
  rerender(
    <CanvasVisualEditor
      {...props}
      mode="style"
      unknown
      onSave={save}
      onClose={close}
    />,
  );
  expect(screen.getByText("保存修改")).toBeDisabled();
  fireEvent.click(screen.getByText("核对保存结果"));
  expect(props.onCheck).toHaveBeenCalled();
});
it("keeps pinned old image revision and changes presentation only", async () => {
  const image = {
      binding_id: "file",
      revision: 2,
      width: 180,
      placement: "top" as const,
      alt: "截图",
    },
    save = vi.fn().mockResolvedValue(true);
  render(
    <CanvasVisualEditor
      {...props}
      node={{ ...a, canvas_data: { image } }}
      mode="image"
      onSave={save}
    />,
  );
  await screen.findByText("当前固定版本（保留）");
  fireEvent.change(screen.getByLabelText("图片宽度"), {
    target: { value: 120 },
  });
  fireEvent.click(screen.getByText("保存修改"));
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith({ image: { ...image, width: 120 } }),
  );
});
it("maps safe style values to node and title presentation", () => {
  expect(nodeStyle()).toEqual({});
  expect(
    nodeStyle({ style: { ...defaultStyle, shape: "rectangle" } }).borderRadius,
  ).toBe(0);
  expect(
    titleStyle({ style: { ...defaultStyle, bold: true, italic: true } }),
  ).toMatchObject({ fontWeight: 700, fontStyle: "italic" });
});

it("keeps range options available after unchecking the last member", () => {
  render(
    <CanvasVisualEditor
      {...props}
      node={{
        ...a,
        canvas_data: { boundary: { members: ["a"], title: "范围" } },
      }}
      mode="boundary"
    />,
  );
  fireEvent.click(screen.getByLabelText("A"));
  expect(screen.getByLabelText("B")).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("B"));
  expect(screen.getByLabelText("B")).toBeChecked();
});

it("distinguishes size changes from color-only formatting", async () => {
  const { visualSizeChanged } = await import("../canvasStyle");
  expect(
    visualSizeChanged(
      { style: defaultStyle },
      { style: { ...defaultStyle, color: "#ff0000" } },
    ),
  ).toBe(false);
  expect(
    visualSizeChanged(
      { style: defaultStyle },
      { style: { ...defaultStyle, font_size: 30 } },
    ),
  ).toBe(true);
  expect(
    visualSizeChanged(
      {},
      {
        image: {
          binding_id: "x",
          revision: 1,
          width: 200,
          aspect: 2,
          placement: "top",
          alt: "",
        },
      },
    ),
  ).toBe(true);
});
it('sets a numeric width without losing node annotations',async()=>{const save=vi.fn().mockResolvedValue(true);render(<CanvasVisualEditor {...props} node={{...a,canvas_data:{note:'保留备注',width:400}}} mode="style" onSave={save}/>);fireEvent.change(screen.getByLabelText('节点宽度'),{target:{value:'360'}});fireEvent.click(screen.getByText('保存修改'));await waitFor(()=>expect(save).toHaveBeenCalledWith(expect.objectContaining({width:360,note:'保留备注'})));});
