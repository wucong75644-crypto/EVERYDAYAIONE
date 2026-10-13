import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import PublishCalendar, { zonedTime } from "../PublishCalendar";
import { socialApi } from "../../../services/socialOps";
vi.mock("../../../services/socialOps", () => ({ socialApi: vi.fn() }));
vi.mock("../CardStudio", () => ({
  SocialAssetPreview: ({ id }: { id: string }) => <span>图片{id}</span>,
}));
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
  id: "schedule1",
  content_id: "content1",
  revision: 3,
  connection_id: "account1",
  check_id: "check1",
  approval_hash: "a".repeat(64),
  state: "scheduled",
  visibility: "private",
  scheduled_at: "2026-11-12T01:00:00Z",
  schedule_timezone: "Asia/Shanghai",
  authorized_until: "2026-11-12T01:01:00Z",
  error: "",
  receipt: {},
  verification: null,
  payload: {
    display_name: "验收账号",
    account_id: "123",
    data: {
      title: "验收测试",
      body: "原版本正文",
      tags: ["手账"],
      assets: [{ id: "asset1", sha256: "digest1" }],
    },
  },
  events: [
    { state: "scheduled", reason: "", created_at: "2026-10-12T01:00:00Z" },
  ],
};
beforeEach(() => vi.mocked(socialApi).mockReset());
describe("scheduled final version", () => {
  it("resolves timezone and rejects DST gaps and overlaps", () => {
    expect(zonedTime("2026-11-12T09:00", "Asia/Shanghai")).toBe(
      "2026-11-12T01:00:00.000Z",
    );
    expect(zonedTime("2026-11-12T09:00", "UTC")).toBe(
      "2026-11-12T09:00:00.000Z",
    );
    expect(() => zonedTime("2026-03-08T02:30", "America/New_York")).toThrow();
    expect(() => zonedTime("2026-11-01T01:30", "America/New_York")).toThrow();
  });
  it("requires reviewing the new summary after edit", async () => {
    const api = vi.mocked(socialApi);
    api.mockImplementation(async (path, method) => {
      if (path === "/publish-schedules/schedule1/revise")
        return { ...row, state: "invalidated" } as never;
      if (path === "/publish-schedules/schedule1" && method === "PUT")
        return {
          ...row,
          state: "awaiting_confirmation",
          approval_hash: "b".repeat(64),
        } as never;
      if (path === "/publish-schedules/schedule1") return row as never;
      return { items: [row], total: 1 } as never;
    });
    render(<PublishCalendar accounts={[]} />);
    fireEvent.click(await screen.findByText("验收测试 · v3"));
    expect(await screen.findByText("原版本正文")).toBeInTheDocument();
    expect(screen.getByText(/授权截止/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("修改排期（原确认失效）"));
    fireEvent.change(await screen.findByLabelText("发布日期与时间"), {
      target: { value: "2026-11-13T10:00" },
    });
    fireEvent.click(screen.getByText("保存并查看新确认摘要"));
    const confirm = await screen.findByText("确认此版本定时发布");
    expect(confirm).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(confirm);
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        "/publish-schedules/schedule1/confirm",
        "POST",
        { approval_hash: "b".repeat(64), acknowledge_risks: true },
      ),
    );
  });
  it("uncertain never offers resend or cancel", async () => {
    const uncertain = { ...row, state: "uncertain" };
    vi.mocked(socialApi).mockImplementation(async (path) =>
      path === "/publish-schedules/schedule1"
        ? (uncertain as never)
        : ({ items: [uncertain], total: 1 } as never),
    );
    render(<PublishCalendar accounts={[]} />);
    fireEvent.click(await screen.findByText("验收测试 · v3"));
    expect(await screen.findByText(/本页面不提供重发操作/)).toBeInTheDocument();
    expect(screen.queryByText("取消尚未发送的排期")).not.toBeInTheDocument();
  });
});
