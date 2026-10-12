import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, it, expect, vi } from "vitest";
import CardStudio from "../CardStudio";
import type { Draft, Run } from "../../../services/socialOps";
const api = vi.hoisted(() => ({ socialApi: vi.fn(), request: vi.fn() }));
vi.mock("../../../services/socialOps", async (original) => ({
  ...(await original<typeof import("../../../services/socialOps")>()),
  socialApi: api.socialApi,
}));
vi.mock("../../../services/api", () => ({ request: api.request }));
vi.mock("../../detail-page/WorkspaceImagePicker", () => ({
  WorkspaceImagePicker: () => null,
}));
const draft: Draft = {
  id: "d1",
  current_revision: 2,
  data: {
    title: "验收测试",
    body: "有证据的正文",
    tags: ["手账"],
    assumptions: [],
    cards: [
      { role: "cover", headline: "封面", body: "" },
      { role: "closing", headline: "收尾", body: "未知事项见正文" },
    ],
    visual: {
      background: "#FFF8F0",
      foreground: "#30343B",
      accent: "#2563EB",
      watermark: "",
    },
    assets: [
      {
        id: "a1",
        sha256: "abc",
        width: 1080,
        height: 1440,
        source: "自主排版",
        rights: "需核对授权",
      },
    ],
  },
  checks: {},
};
const runs = [
  {
    id: "r1",
    kind: "check",
    status: "completed",
    result: { content_id: "d1", revision: 2 },
  },
] as unknown as Run[];
const props = {
  draft,
  dirty: false,
  jobId: "j1",
  runs,
  accounts: [
    {
      id: "c1",
      platform: "xiaohongshu",
      display_name: "验收测试账号",
      login_state: "connected",
    },
  ],
  skills: [],
  onRefresh: vi.fn().mockResolvedValue(undefined),
};
let state = "awaiting_confirmation";
const intent = () => ({
  id: "i1",
  state,
  approval_hash: "hash",
  revision: 2,
  visibility: "private",
  payload: {
    display_name: "验收测试账号",
    account_id: "test",
    data: draft.data,
  },
  receipt: {},
});
beforeEach(() => {
  vi.clearAllMocks();
  state = "awaiting_confirmation";
  api.request.mockReturnValue(Promise.resolve(new Blob()));
  vi.stubGlobal(
    "URL",
    Object.assign(URL, {
      createObjectURL: vi.fn(() => "blob:test"),
      revokeObjectURL: vi.fn(),
    }),
  );
  api.socialApi.mockImplementation(async (path: string, method?: string) => {
    if (path.endsWith("/publish-intents"))
      return method === "POST" ? intent() : [];
    if (path.endsWith("/package-checks"))
      return {
        id: "ch1",
        revision: 2,
        structural: {
          status: "ready_for_confirmation",
          hard_errors: [],
          warnings: [],
          unknowns: ["图片授权未知"],
        },
      };
    if (path.endsWith("/confirm")) {
      state = "submitted";
      return intent();
    }
    return { id: "run1", status: "queued" };
  });
});
it("requires current semantic check and final operator acknowledgement before publishing", async () => {
  render(<CardStudio {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "检查当前整包" }));
  await screen.findByText("可进入最终确认");
  fireEvent.change(screen.getByLabelText("发布账号"), {
    target: { value: "c1" },
  });
  fireEvent.click(screen.getByRole("button", { name: "查看最终发布确认" }));
  await screen.findByRole("region", { name: "最终发布确认" });
  expect(
    screen.getByRole("button", { name: "确认最终版本并立即发布" }),
  ).toBeDisabled();
  expect(
    api.socialApi.mock.calls.some(([p]) => String(p).endsWith("/confirm")),
  ).toBe(false);
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(
    screen.getByRole("button", { name: "确认最终版本并立即发布" }),
  );
  await screen.findByText("已提交待核验");
  expect(api.socialApi).toHaveBeenCalledWith(
    "/publish-intents/i1/confirm",
    "POST",
    { approval_hash: "hash", acknowledge_risks: true },
  );
  expect(
    screen.getByRole("button", { name: "确认最终版本并立即发布" }),
  ).toBeDisabled();
  expect(screen.queryByText("已核验发布（人工）")).toBeNull();
});
it("invalidates local confirmation when card text changes and after a new revision", async () => {
  const view = render(<CardStudio {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "检查当前整包" }));
  await screen.findByText("可进入最终确认");
  fireEvent.change(screen.getByLabelText("卡片1标题"), {
    target: { value: "改过的封面" },
  });
  expect(screen.getByRole("button", { name: "检查当前整包" })).toBeDisabled();
  expect(
    screen.getByRole("button", { name: "查看最终发布确认" }),
  ).toBeDisabled();
  view.rerender(
    <CardStudio {...props} draft={{ ...draft, current_revision: 3 }} />,
  );
  expect(screen.queryByText("可进入最终确认")).toBeNull();
  expect(screen.getByRole("button", { name: "检查当前整包" })).toBeDisabled();
});
it("blocks preparation while text is unsaved and never exposes automatic publishing", () => {
  render(<CardStudio {...props} dirty />);
  expect(
    screen.getByRole("button", { name: "制作并保存卡片新版本" }),
  ).toBeDisabled();
  expect(screen.queryByRole("button", { name: "自动发布" })).toBeNull();
  expect(screen.queryByRole("button", { name: "定时发布" })).toBeNull();
});
it("reuses the same request identity for repeated card-generation clicks", async () => {
  render(
    <CardStudio
      {...props}
      skills={["easel-card-design", "easel-card-xiaohongshu"].map(
        (skill_id) => ({ skill_id, revision: "v1", name: skill_id }),
      )}
    />,
  );
  for (const id of ["easel-card-design", "easel-card-xiaohongshu"])
    fireEvent.change(screen.getByLabelText(id), { target: { value: "v1" } });
  fireEvent.click(screen.getByRole("button", { name: "AI 设计并制作卡片" }));
  await screen.findByText(/已创建有预算上限/);
  fireEvent.click(screen.getByRole("button", { name: "AI 设计并制作卡片" }));
  await waitFor(() =>
    expect(
      api.socialApi.mock.calls.filter(([p]) => p === "/production-runs"),
    ).toHaveLength(2),
  );
  const calls = api.socialApi.mock.calls.filter(
    ([p]) => p === "/production-runs",
  );
  expect(calls[0][2].client_request_id).toBe(calls[1][2].client_request_id);
});
