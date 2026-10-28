import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
} from "@testing-library/react";
import { afterEach, it, expect, vi } from "vitest";
import {
  CanvasMediaCard,
  CanvasMediaEditor,
  CanvasMediaPreview,
} from "../CanvasMedia";
import { knowledgeFilesApi } from "../../../services/knowledge";
import type { KnowledgeNode } from "../../../services/knowledge";
vi.mock("../../../services/knowledge", () => ({
  knowledgeFilesApi: {
    list: vi.fn().mockResolvedValue([]),
    content: vi.fn(),
    playback: vi.fn(),
    upload: vi.fn(),
  },
}));
const node: KnowledgeNode = {
  id: "a",
  node_id: "la",
  container_id: "c",
  parent_id: null,
  title: "培训",
  kind: "topic",
  revision: 1,
  layout_revision: 1,
  x: 0,
  y: 0,
};
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
it("keeps ordinary website links as safe new-tab navigation", () => {
  render(
    <CanvasMediaCard
      node={{
        ...node,
        canvas_data: {
          link: {
            url: "https://example.com/guide",
            title: "操作手册",
            kind: "web",
          },
        },
      }}
      writable={false}
      onEdit={vi.fn()}
      onView={vi.fn()}
    />,
  );
  const link = screen.getByRole("link");
  expect(link).toHaveAttribute("target", "_blank");
  expect(link).toHaveAttribute("rel", "noopener noreferrer");
  expect(screen.queryByRole("button")).toBeNull();
  expect(document.querySelector("iframe")).toBeNull();
});
it("distinguishes an uploaded video from an external video on the same topic", () => {
  const view = vi.fn();
  render(
    <CanvasMediaCard
      node={{
        ...node,
        canvas_data: {
          video: { binding_id: "file", revision: 2, title: "课程" },
          link: {
            url: "https://youtu.be/M7lc1UVf-VE",
            title: "外部课程",
            kind: "auto",
          },
        },
      }}
      writable
      onEdit={vi.fn()}
      onView={view}
    />,
  );
  fireEvent.click(screen.getByLabelText("播放节点视频 培训"));
  expect(view).toHaveBeenLastCalledWith("video");
  fireEvent.click(screen.getByLabelText("预览视频链接 培训"));
  expect(view).toHaveBeenLastCalledWith("link");
});
it("does not load private video bytes before opening the viewer", () => {
  render(
    <CanvasMediaCard
      node={{
        ...node,
        canvas_data: {
          video: { binding_id: "file", revision: 2, title: "课程" },
        },
      }}
      writable={false}
      onEdit={vi.fn()}
      onView={vi.fn()}
    />,
  );
  expect(knowledgeFilesApi.content).not.toHaveBeenCalled();
});
it("provides an original-platform exit beside the controlled embed", () => {
  render(
    <CanvasMediaPreview
      node={{
        ...node,
        canvas_data: {
          link: {
            url: "https://youtu.be/M7lc1UVf-VE",
            title: "外部课程",
            kind: "auto",
          },
        },
      }}
      source="link"
      onClose={vi.fn()}
    />,
  );
  expect(screen.getByTitle("视频预览 · YouTube")).toHaveAttribute(
    "src",
    "https://www.youtube-nocookie.com/embed/M7lc1UVf-VE?autoplay=0",
  );
  expect(screen.getByRole("link", { name: /前往原平台/ })).toHaveAttribute(
    "href",
    "https://youtu.be/M7lc1UVf-VE",
  );
});
it("keeps direct-video failures recoverable through the source link", () => {
  render(
    <CanvasMediaPreview
      node={{
        ...node,
        canvas_data: {
          link: {
            url: "https://example.com/a.mp4",
            title: "视频",
            kind: "video",
          },
        },
      }}
      source="link"
      onClose={vi.fn()}
    />,
  );
  fireEvent.error(screen.getByLabelText("节点视频播放器"));
  expect(screen.getByRole("alert")).toHaveTextContent("视频直链无法播放");
  expect(screen.getByRole("link")).toHaveAttribute(
    "href",
    "https://example.com/a.mp4",
  );
});
it("retains edited URL after rejected saves", async () => {
  const save = vi.fn().mockResolvedValue(false),
    close = vi.fn();
  render(
    <CanvasMediaEditor
      node={node}
      mode="link"
      pending={false}
      unknown={false}
      saveError="保存失败"
      onCheck={vi.fn()}
      onUploaded={vi.fn()}
      onSave={save}
      onClose={close}
    />,
  );
  fireEvent.change(screen.getByLabelText("节点网址"), {
    target: { value: "https://example.com/a.mp4" },
  });
  fireEvent.click(screen.getByText("保存链接"));
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith({
      link: { url: "https://example.com/a.mp4", title: "", kind: "auto" },
    }),
  );
  expect(close).not.toHaveBeenCalled();
  expect(screen.getByLabelText("节点网址")).toHaveValue(
    "https://example.com/a.mp4",
  );
});
it("rejects unsafe URLs and locks unknown saves while offering receipt checking", () => {
  const save = vi.fn(),
    check = vi.fn();
  const { rerender } = render(
    <CanvasMediaEditor
      node={node}
      mode="link"
      pending={false}
      unknown={false}
      saveError=""
      onCheck={check}
      onUploaded={vi.fn()}
      onSave={save}
      onClose={vi.fn()}
    />,
  );
  fireEvent.change(screen.getByLabelText("节点网址"), {
    target: { value: "javascript:alert(1)" },
  });
  fireEvent.click(screen.getByText("保存链接"));
  expect(save).not.toHaveBeenCalled();
  expect(screen.getByRole("alert")).toHaveTextContent("HTTP");
  rerender(
    <CanvasMediaEditor
      node={node}
      mode="link"
      pending={false}
      unknown
      saveError="结果未知"
      onCheck={check}
      onUploaded={vi.fn()}
      onSave={save}
      onClose={vi.fn()}
    />,
  );
  expect(screen.getByLabelText("节点网址")).toBeDisabled();
  fireEvent.click(screen.getByText("核对保存结果"));
  expect(check).toHaveBeenCalled();
});

it("streams the exact version only after clicking and releases the player on close", async () => {
 vi.stubGlobal('IntersectionObserver',undefined);vi.mocked(knowledgeFilesApi.playback).mockResolvedValue({url:'/private/stream',expires_in:300});const pause=vi.spyOn(HTMLMediaElement.prototype,'pause').mockImplementation(()=>{}),load=vi.spyOn(HTMLMediaElement.prototype,'load').mockImplementation(()=>{});
 const view=render(<CanvasMediaPreview node={{...node,canvas_data:{video:{binding_id:'file',revision:3,title:'课程'}}}} source="video" onClose={vi.fn()}/>);
 expect(knowledgeFilesApi.playback).not.toHaveBeenCalled();fireEvent.click(screen.getByRole('button',{name:'播放视频 课程'}));await waitFor(()=>expect(document.querySelector('video')).toBeTruthy());const video=document.querySelector('video')!;expect(video).toHaveAttribute('src','/private/stream');expect(knowledgeFilesApi.content).not.toHaveBeenCalled();expect(knowledgeFilesApi.playback).toHaveBeenCalledWith('a','file',3,expect.any(AbortSignal),'preview');view.unmount();expect(pause).toHaveBeenCalled();expect(load).toHaveBeenCalled();expect(video).not.toHaveAttribute('src');vi.unstubAllGlobals();
});
it("retries an uncertain upload with the same command id before enabling save", async () => {
  vi.mocked(knowledgeFilesApi.upload)
    .mockRejectedValueOnce(new Error("断网"))
    .mockResolvedValue({ success: true, data: { binding_id: "uploaded" } });
  vi.mocked(knowledgeFilesApi.list).mockResolvedValue([
    {
      id: "uploaded",
      title: "课程.mp4",
      current_revision: 1,
      version: { mime_type: "video/mp4" },
    } as never,
  ]);
  render(
    <CanvasMediaEditor
      node={node}
      mode="video"
      pending={false}
      unknown={false}
      saveError=""
      onCheck={vi.fn()}
      onUploaded={vi.fn().mockResolvedValue(undefined)}
      onSave={vi.fn()}
      onClose={vi.fn()}
    />,
  );
  fireEvent.change(screen.getByLabelText("上传原视频"), {
    target: { files: [new File(["video"], "课程.mp4", { type: "video/mp4" })] },
  });
  await screen.findByText("核对上传结果");
  expect(screen.getByText("保存视频")).toBeDisabled();
  const first = vi.mocked(knowledgeFilesApi.upload).mock.calls[0];
  fireEvent.click(screen.getByText("核对上传结果"));
  await waitFor(() => expect(screen.getByText("保存视频")).toBeEnabled());
  expect(vi.mocked(knowledgeFilesApi.upload).mock.calls[1][2]).toBe(first[2]);
});
it("reports revoked file access instead of displaying a public fallback", async () => {
  vi.stubGlobal("IntersectionObserver",undefined);vi.mocked(knowledgeFilesApi.playback).mockRejectedValue(new Error("检查权限"));
  render(
    <CanvasMediaPreview
      node={{
        ...node,
        canvas_data: {
          video: { binding_id: "private", revision: 1, title: "课程" },
        },
      }}
      source="video"
      onClose={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("button",{name:"播放视频 课程"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("检查权限");
  expect(screen.queryByRole("link")).toBeNull();
});
