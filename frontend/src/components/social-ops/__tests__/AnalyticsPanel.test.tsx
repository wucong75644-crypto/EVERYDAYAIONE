import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import AnalyticsPanel, { metricText } from "../AnalyticsPanel";
import { socialApi } from "../../../services/socialOps";
vi.mock("../../../services/socialOps", () => ({ socialApi: vi.fn() }));
vi.mock("../../common/Modal", () => ({
  default: ({
    isOpen,
    children,
  }: {
    isOpen: boolean;
    children: React.ReactNode;
  }) => (isOpen ? <div>{children}</div> : null),
}));
const row = {
  id: "intent",
  title: "已核验内容",
  revision: 2,
  connection_id: "a",
  account_id: "account",
  state: "verified",
  verification: { remote_id: "remote" },
  latest: {
    id: "m",
    metrics: { likes: 0, comments: null },
    observed_at: "2026-10-13T00:00:00Z",
  },
  collection: null,
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(socialApi).mockImplementation(async (path) => {
    if (path === "/analytics/capabilities") return { xiaohongshu: {} };
    if (path.startsWith("/analytics/contents?")) return [row];
    if (path === "/skill-options")
      return [
        {
          skill_id: "easel-content-postmortem",
          revision: "fixed",
          name: "内容复盘",
        },
      ];
    if (path === "/analytics/reviews" || path === "/analytics/schedules")
      return [];
    return {};
  });
});
async function open() {
  render(<AnalyticsPanel accounts={[]} profiles={[]} />);
  fireEvent.click(screen.getByText(/数据回收、内容复盘/));
  await screen.findByText("已核验内容 v2");
}
describe("analytics workspace", () => {
  it("distinguishes null and actual zero and never defaults missing to zero", async () => {
    expect(metricText(null)).toBe("缺失");
    expect(metricText(0)).toBe("0");
    await open();
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(screen.getAllByText("缺失").length).toBeGreaterThan(0);
  });
  it("requires explicit content selection, time range and pinned skill before report", async () => {
    await open();
    expect(screen.getByText("生成所选内容报告")).toBeDisabled();
    fireEvent.click(screen.getByLabelText("选择已核验内容"));
    fireEvent.change(screen.getByLabelText("起始日期"), {
      target: { value: "2026-10-01" },
    });
    fireEvent.change(screen.getByLabelText("结束日期"), {
      target: { value: "2026-10-13" },
    });
    fireEvent.change(screen.getByLabelText("固定审核Skill版本"), {
      target: { value: "fixed" },
    });
    await waitFor(() =>
      expect(screen.getByText("生成所选内容报告")).not.toBeDisabled(),
    );
    fireEvent.click(screen.getByText("生成所选内容报告"));
    await waitFor(() =>
      expect(socialApi).toHaveBeenCalledWith(
        "/analytics/reviews",
        "POST",
        expect.objectContaining({
          intent_ids: ["intent"],
          stages: [{ skill_id: "easel-content-postmortem", revision: "fixed" }],
        }),
      ),
    );
  });
  it("keeps collection disabled until a full link is supplied", async () => {
    await open();
    fireEvent.click(screen.getByText("回收/周期"));
    expect(screen.getByText("开始一次回收")).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/完整采集链接/), {
      target: {
        value: "https://www.xiaohongshu.com/explore/remote?xsec_token=fixture",
      },
    });
    expect(screen.getByText("开始一次回收")).not.toBeDisabled();
  });
});

it("shows import row errors and blocks invalid confirmation", async () => {
  const base = vi.mocked(socialApi).getMockImplementation()!;
  vi.mocked(socialApi).mockImplementation(async (path, method, data) =>
    path === "/analytics/imports/preview"
      ? {
          id: "batch",
          preview_hash: "hash",
          valid: false,
          duplicate_count: 0,
          rules: "整批拒绝",
          rows: [
            {
              line: 2,
              errors: ["账号或平台内容ID不符"],
              duplicate: false,
              data: { remote_id: "remote", metrics: { likes: 0 } },
            },
          ],
        }
      : base(path, method, data),
  );
  await open();
  fireEvent.click(screen.getByText("CSV / Excel 导入（先预览后确认）"));
  fireEvent.change(screen.getByLabelText("文件"), {
    target: {
      files: [new File(["likes\n0"], "metrics.csv", { type: "text/csv" })],
    },
  });
  fireEvent.change(screen.getByLabelText("来源说明"), {
    target: { value: "测试后台导出" },
  });
  fireEvent.click(screen.getByText("预览导入"));
  await screen.findByText(/账号或平台内容ID不符/);
  expect(screen.getByText("确认当前预览")).toBeDisabled();
});

it.each(["accept", "reject"])(
  "keeps suggestion evidence and sends explicit %s decision",
  async (decision) => {
    const base = vi.mocked(socialApi).getMockImplementation()!;
    const report = {
      id: "r",
      kind: "postmortem",
      status: "completed",
      error: "",
      input: {
        profile_revision_id: "pr",
        profile_id: "p",
        contents: [{ revision: 2 }],
        sources: [
          {
            id: "m",
            body: "真实指标片段",
            coverage: { content_kind: "metrics" },
            source_url: "",
          },
        ],
      },
      outputs: [],
      usage: {},
      suggestions: [
        {
          id: "s",
          kind: "experience",
          original_text: "尝试更清楚的说明",
          decision: "pending",
          evidence: [{ snapshot_id: "m", quote: "真实指标片段" }],
          result: null,
        },
      ],
      result: {
        observed_facts: [
          { id: "f", snapshot_id: "m", metric: "likes", value: 0 },
        ],
        possible_causes: [],
        comment_insights: [],
        insufficient_evidence: ["样本不足"],
        statistics: {},
        comment_coverage: { selected: 0 },
      },
    };
    vi.mocked(socialApi).mockImplementation(async (path, method, data) =>
      path === "/analytics/reviews"
        ? [report]
        : path === "/analytics/reviews/r"
          ? report
          : base(path, method, data),
    );
    render(
      <AnalyticsPanel
        accounts={[]}
        profiles={[
          {
            id: "p",
            revision_id: "pr",
            current_revision: 1,
            data: {
              name: "测试",
              positioning: "专业",
              audience: "读者",
              facts: "",
              pillars: [],
              tone: "",
              prohibited: "",
              experience: "",
              visual_template: "",
            },
          },
        ]}
      />,
    );
    fireEvent.click(screen.getByText(/数据回收、内容复盘/));
    fireEvent.click(await screen.findByText("内容复盘：completed"));
    await screen.findByText("原建议：尝试更清楚的说明");
    fireEvent.click(screen.getByText("查看证据 1"));
    expect(screen.getAllByText(/真实指标片段/).length).toBeGreaterThan(0);
    fireEvent.change(screen.getByLabelText("编辑后采用"), {
      target: { value: "操作者编辑后的经验" },
    });
    fireEvent.click(
      screen.getByText(decision === "accept" ? "确认接受" : "拒绝"),
    );
    await waitFor(() =>
      expect(socialApi).toHaveBeenCalledWith(
        "/analytics/suggestions/s/decision",
        "POST",
        { decision, text: "操作者编辑后的经验", expected_profile_revision: 1 },
      ),
    );
  },
);
