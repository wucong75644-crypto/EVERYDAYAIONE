import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, it, expect, vi } from "vitest";
import { ApiRequestError } from "../../services/api";
import SocialOps from "../SocialOps";
const api = vi.hoisted(() => ({ socialApi: vi.fn(), connections: vi.fn() }));
vi.mock("../../services/socialOps", async (original) => ({
  ...(await original<typeof import("../../services/socialOps")>()),
  ...api,
}));
const job = {
  id: "job1",
  generation: 1,
  status: "completed",
  connection_id: "c1",
  last_error: "",
  next_eligible_at: "2026-10-11T00:00:00Z",
  next_occurrence_at: null,
  plan: { keywords: ["测试研究"], sample_urls: [] },
  items: [
    {
      id: "i1",
      generation: 1,
      action: "read",
      page: 1,
      status: "completed",
      attempts: 1,
      error: "",
    },
  ],
};
const source = {
  id: "s1",
  title: "来源标题",
  body: "<script>alert(1)</script>这是原文",
  source_url: "https://www.xiaohongshu.com/explore/abc",
  generation: 1,
  fetched_at: "2026-10-11T00:00:00Z",
  coverage: {
    content_kind: "full_text",
    missing: ["运营指标"],
    warnings: [],
    scope: "指定样本",
    truncated: false,
  },
};
const draft = {
  id: "d1",
  current_revision: 1,
  data: {
    title: "草稿标题",
    body: "有据正文",
    tags: ["标签"],
    evidence_ids: ["s1"],
    assumptions: [],
  },
  checks: { status: "needs_media", ready_to_publish: false },
};
beforeEach(() => {
  vi.clearAllMocks();
  api.connections.mockResolvedValue([]);
  api.socialApi.mockImplementation(async (path: string) => {
    if (path === "/profiles") return [];
    if (path === "/research-jobs") return [job];
    if (path === "/skill-options") return [];
    if (path === "/research-jobs/job1") return job;
    if (path.endsWith("/sources")) return [source];
    if (path.endsWith("/production-runs")) return [];
    if (path.endsWith("/contents")) return [draft];
    if (path.startsWith("/topics?"))
      return [
        {
          id: "t1",
          revision: 1,
          data: {
            title: "有据选题",
            angle: "具体角度",
            audience: "读者",
            format: "图文",
            planned_date: null,
            decision: "adopt",
            evidence: [{ snapshot_id: "s1", quote: "这是原文" }],
            unknowns: ["需求量未知"],
            required_materials: ["自有图"],
          },
        },
      ];
    return {};
  });
});
const mount = () =>
  render(
    <MemoryRouter>
      <SocialOps />
    </MemoryRouter>,
  );
describe("SocialOps", () => {
  it("shows unsupported data boundaries and escaped source text", async () => {
    mount();
    await screen.findByRole("option", { name: /测试研究/ });
    fireEvent.change(screen.getByLabelText("选择研究任务"), {
      target: { value: "job1" },
    });
    expect(await screen.findByText(/来源标题/)).toBeTruthy();
    expect(screen.getByText(/不支持完整主页同步/)).toBeTruthy();
    expect(screen.getByText("<script>alert(1)</script>这是原文")).toBeTruthy();
    expect(document.querySelector("script")).toBeNull();
  });
  it("requires reviewed skill versions and never triggers a platform publish", async () => {
    mount();
    await screen.findByRole("option", { name: /测试研究/ });
    fireEvent.click(screen.getByRole("button", { name: "选题计划" }));
    fireEvent.change(screen.getByLabelText("研究任务"), {
      target: { value: "job1" },
    });
    await screen.findByText("有据选题");
    fireEvent.click(
      screen.getByRole("button", { name: "分析样本并生成选题计划" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "缺少已审核的技能版本",
    );
    expect(
      api.socialApi.mock.calls.some(
        (c) => c[0] === "/production-runs" && c[1] === "POST",
      ),
    ).toBe(false);
  });
  it("saves an edited draft as a new version and keeps media missing", async () => {
    mount();
    await screen.findByRole("option", { name: /测试研究/ });
    fireEvent.click(screen.getByRole("button", { name: "文案编辑" }));
    fireEvent.change(screen.getByLabelText("研究任务"), {
      target: { value: "job1" },
    });
    await screen.findByRole("option", { name: /草稿标题/ });
    fireEvent.change(screen.getByLabelText("选择草稿"), {
      target: { value: "d1" },
    });
    fireEvent.change(screen.getByLabelText("标题"), {
      target: { value: "修改标题" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存新版本" }));
    await waitFor(() =>
      expect(api.socialApi).toHaveBeenCalledWith(
        "/contents/d1/revisions",
        "POST",
        expect.objectContaining({
          expected_revision: 1,
          title: "修改标题",
          evidence_ids: ["s1"],
        }),
      ),
    );
    expect(screen.getByText("待补图片素材")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "发布" })).toBeNull();
  });
});

it("服务不可用时不误报暂无账号，并阻止创建", async () => {
  api.socialApi.mockRejectedValue(
    new ApiRequestError("DISABLED", "Request failed with status code 503", 503),
  );
  mount();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "社媒运营尚未启用或服务暂不可用",
  );
  expect(screen.queryByText(/暂无可用账号/)).toBeNull();
  expect(screen.getByRole("button", { name: "创建并开始采集" })).toBeDisabled();
});
it("编辑未保存时禁用对旧版本的检查，预览可切换", async () => {
  mount();
  await screen.findByRole("option", { name: /测试研究/ });
  fireEvent.click(screen.getByRole("button", { name: "文案编辑" }));
  fireEvent.change(screen.getByLabelText("研究任务"), {
    target: { value: "job1" },
  });
  await screen.findByRole("option", { name: /草稿标题/ });
  fireEvent.change(screen.getByLabelText("选择草稿"), {
    target: { value: "d1" },
  });
  fireEvent.change(screen.getByLabelText("标题"), {
    target: { value: "未保存标题" },
  });
  expect(screen.getByText("有未保存修改")).toBeTruthy();
  expect(screen.getByRole("button", { name: "检查已保存版本" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "预览" }));
  expect(screen.getByRole("heading", { name: "未保存标题" })).toBeVisible();
  expect(screen.getByText("待补图片素材")).toBeVisible();
});

it("取消放弃未保存修改后，新建任务不清空当前草稿", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  mount();
  await screen.findByRole("option", { name: /测试研究/ });
  fireEvent.click(screen.getByRole("button", { name: "文案编辑" }));
  fireEvent.change(screen.getByLabelText("研究任务"), {
    target: { value: "job1" },
  });
  await screen.findByRole("option", { name: /草稿标题/ });
  fireEvent.change(screen.getByLabelText("选择草稿"), {
    target: { value: "d1" },
  });
  fireEvent.change(screen.getByLabelText("标题"), {
    target: { value: "保留未保存内容" },
  });
  fireEvent.click(screen.getByRole("button", { name: "新建研究任务" }));
  expect(confirm).toHaveBeenCalled();
  expect(screen.getByLabelText("标题")).toHaveValue("保留未保存内容");
  confirm.mockRestore();
});
