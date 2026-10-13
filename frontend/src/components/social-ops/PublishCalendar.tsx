import { useEffect, useState } from "react";
import { socialApi, type Connection } from "../../services/socialOps";
import { SocialAssetPreview } from "./CardStudio";
import Modal from "../common/Modal";

import {
  publishStates,
  ScheduleFields,
  zonedTime,
} from "./PublishScheduleControls";
export { zonedTime } from "./PublishScheduleControls";

type Schedule = {
  id: string;
  content_id: string;
  revision: number;
  connection_id: string;
  check_id: string;
  approval_hash: string;
  state: string;
  visibility: string;
  scheduled_at: string;
  schedule_timezone: string;
  authorized_until: string;
  error: string;
  receipt: unknown;
  verification: unknown;
  events?: {
    state: string;
    reason: string;
    created_at: string;
    actor_id?: string;
    snapshot?: unknown;
  }[];
  payload: {
    display_name: string;
    account_id: string;
    data: {
      title: string;
      body: string;
      tags: string[];
      assets: { id: string; sha256: string }[];
    };
  };
};
export default function PublishCalendar({
  accounts,
}: {
  accounts: Connection[];
}) {
  const [rows, setRows] = useState<Schedule[]>([]),
    [total, setTotal] = useState(0),
    [page, setPage] = useState(0),
    [view, setView] = useState("list"),
    [month, setMonth] = useState(new Date().toISOString().slice(0, 7));
  const [selected, setSelected] = useState<Schedule | null>(null),
    [editing, setEditing] = useState(false),
    [time, setTime] = useState(""),
    [zone, setZone] = useState("Asia/Shanghai"),
    [grace, setGrace] = useState(60),
    [account, setAccount] = useState(""),
    [visibility, setVisibility] = useState("private"),
    [ack, setAck] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const load = async () => {
    const result = await socialApi<{ items: Schedule[]; total: number }>(
      `/publish-schedules?offset=${page * 30}&limit=30${view === "calendar" ? "&month=" + month : ""}`,
    );
    if (!Array.isArray(result?.items))
      throw new Error("排期列表响应不可用，请刷新");
    setRows(result.items);
    setTotal(result.total);
  };
  useEffect(() => {
    void load().catch((e) => setError(String(e)));
  }, [page, view, month]);
  const act = async (fn: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const open = async (id: string) => {
    const row = await socialApi<Schedule>(`/publish-schedules/${id}`);
    setSelected(row);
    setEditing(false);
    setAck(false);
    setAccount(row.connection_id);
    setVisibility(row.visibility);
    setZone(row.schedule_timezone);
    setGrace(
      (Date.parse(row.authorized_until) - Date.parse(row.scheduled_at)) / 1000,
    );
    setTime(
      new Intl.DateTimeFormat("sv-SE", {
        timeZone: row.schedule_timezone,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
      })
        .format(new Date(row.scheduled_at))
        .replace(" ", "T"),
    );
  };
  const card = (row: Schedule) => (
    <button
      key={row.id}
      className="text-left border border-border-default hover:bg-surface rounded-xl p-3 w-full space-y-1"
      onClick={() => void act(() => open(row.id))}
    >
      <strong>
        {row.payload.data.title} · v{row.revision}
      </strong>
      <p>{publishStates[row.state] ?? row.state}</p>
      <p className="text-sm">
        {row.payload.display_name} ·{" "}
        {new Date(row.scheduled_at).toLocaleString("zh-CN", {
          timeZone: row.schedule_timezone,
        })}{" "}
        ({row.schedule_timezone})
      </p>
      {row.error && <p className="text-amber-700">{row.error}</p>}
    </button>
  );
  const visible = rows.filter(
    (r) =>
      new Intl.DateTimeFormat("sv-SE", { timeZone: r.schedule_timezone })
        .format(new Date(r.scheduled_at))
        .slice(0, 7) === month,
  );
  return (
    <section
      className="border border-border-default bg-surface-card rounded-2xl p-4 space-y-3"
      aria-label="发布排期与日历"
    >
      <div className="flex flex-wrap gap-3 items-center">
        <h3 className="font-semibold">发布排期与日历</h3>
        <button
          onClick={() => {
            setView("list");
            setPage(0);
          }}
        >
          列表
        </button>
        <button
          onClick={() => {
            setView("calendar");
            setPage(0);
          }}
        >
          日历
        </button>
        <button disabled={busy} onClick={() => void act(load)}>
          刷新排期
        </button>
      </div>
      {error && !selected && (
        <p role="alert" className="text-red-700">
          {error}
        </p>
      )}
      {!rows.length && <p>尚无发布排期。请在完整图文确认中选择定时发布。</p>}
      {view === "list" ? (
        rows.map(card)
      ) : (
        <>
          <input
            aria-label="日历月份"
            type="month"
            value={month}
            onChange={(e) => {
              setMonth(e.target.value);
              setPage(0);
            }}
          />
          <p className="text-sm">
            按每条排期时区展示日期；日历显示当前分页记录，可翻页查看其他排期。
          </p>
          <div className="grid sm:grid-cols-3 gap-2">
            {Array.from(
              new Set(
                visible.map((r) =>
                  new Intl.DateTimeFormat("sv-SE", {
                    timeZone: r.schedule_timezone,
                  }).format(new Date(r.scheduled_at)),
                ),
              ),
            )
              .sort()
              .map((day) => (
                <div className="border rounded-lg p-2" key={day}>
                  <h4>{day}</h4>
                  {visible
                    .filter(
                      (r) =>
                        new Intl.DateTimeFormat("sv-SE", {
                          timeZone: r.schedule_timezone,
                        }).format(new Date(r.scheduled_at)) === day,
                    )
                    .map(card)}
                </div>
              ))}
          </div>
        </>
      )}
      <div className="flex gap-3">
        <button disabled={!page || busy} onClick={() => setPage(page - 1)}>
          上一页
        </button>
        <span>
          {page + 1}页 · 共{total}条
        </span>
        <button
          disabled={(page + 1) * 30 >= total || busy}
          onClick={() => setPage(page + 1)}
        >
          下一页
        </button>
      </div>
      <Modal
        isOpen={!!selected}
        onClose={() => setSelected(null)}
        title="排期最终版本与运行记录"
        maxWidth="max-w-4xl"
      >
        {selected && (
          <div className="space-y-3">
            {error && (
              <p role="alert" className="text-red-700">
                {error}
              </p>
            )}
            <strong>{publishStates[selected.state]}</strong>
            <p>
              {selected.payload.display_name} · {selected.payload.account_id} ·
              v{selected.revision} ·{" "}
              {selected.visibility === "public" ? "公开" : "仅自己可见"}
            </p>
            <p>
              {new Date(selected.scheduled_at).toLocaleString("zh-CN", {
                timeZone: selected.schedule_timezone,
              })}{" "}
              ({selected.schedule_timezone})，授权截止{" "}
              {new Date(selected.authorized_until).toLocaleString("zh-CN", {
                timeZone: selected.schedule_timezone,
              })}
            </p>
            <h4>{selected.payload.data.title}</h4>
            <p className="whitespace-pre-wrap">{selected.payload.data.body}</p>
            <p>{selected.payload.data.tags.map((t) => "#" + t).join(" ")}</p>
            <div className="grid grid-cols-3 gap-2">
              {selected.payload.data.assets.map((a, i) => (
                <div key={a.id}>
                  <SocialAssetPreview id={a.id} />
                  <p className="text-xs break-all">
                    第{i + 1}张 · {a.sha256}
                  </p>
                </div>
              ))}
            </div>
            {selected.error && (
              <p className="text-amber-700">{selected.error}</p>
            )}
            <p>平台审核状态：未知；提交完成不等于已公开发布。</p>
            <fieldset disabled={busy} className="space-y-3">
              {[
                "scheduled",
                "waiting_budget",
                "paused",
                "invalidated",
                "awaiting_confirmation",
                "preparing",
              ].includes(selected.state) && (
                <>
                  <button
                    className="mr-3 rounded-lg border border-border-default px-3 py-2"
                    onClick={() =>
                      void act(async () => {
                        const revoked = await socialApi<Schedule>(
                          `/publish-schedules/${selected.id}/revise`,
                          "POST",
                        );
                        setSelected(revoked);
                        setEditing(true);
                        setAck(false);
                      })
                    }
                  >
                    修改排期（原确认失效）
                  </button>
                  <button
                    className="rounded-lg border border-border-default px-3 py-2"
                    onClick={() =>
                      void act(async () => {
                        await socialApi(
                          `/publish-intents/${selected.id}/cancel`,
                          "POST",
                        );
                        await open(selected.id);
                      })
                    }
                  >
                    取消尚未发送的排期
                  </button>
                </>
              )}
              {editing && (
                <>
                  <ScheduleFields
                    time={time}
                    zone={zone}
                    grace={grace}
                    onChange={(t, z, g) => {
                      setTime(t);
                      setZone(z);
                      setGrace(g);
                      setAck(false);
                    }}
                  />
                  <label>
                    目标账号
                    <select
                      aria-label="排期目标账号"
                      value={account}
                      onChange={(e) => setAccount(e.target.value)}
                    >
                      {accounts
                        .filter((a) => a.platform === "xiaohongshu")
                        .map((a) => (
                          <option key={a.id} value={a.id}>
                            {a.display_name}
                          </option>
                        ))}
                    </select>
                  </label>
                  <label>
                    可见范围
                    <select
                      aria-label="排期可见范围"
                      value={visibility}
                      onChange={(e) => setVisibility(e.target.value)}
                    >
                      <option value="private">仅自己可见</option>
                      <option value="public">公开</option>
                    </select>
                  </label>
                  <button
                    className="social-primary"
                    onClick={() =>
                      void act(async () => {
                        const row = await socialApi<Schedule>(
                          `/publish-schedules/${selected.id}`,
                          "PUT",
                          {
                            content_id: selected.content_id,
                            expected_revision: selected.revision,
                            connection_id: account,
                            check_id: selected.check_id,
                            visibility,
                            scheduled_at: zonedTime(time, zone),
                            timezone: zone,
                            grace_seconds: grace,
                          },
                        );
                        setSelected(row);
                        setEditing(false);
                        setAck(false);
                      })
                    }
                  >
                    保存并查看新确认摘要
                  </button>
                </>
              )}
              {!editing && selected.state === "awaiting_confirmation" && (
                <>
                  <label>
                    <input
                      type="checkbox"
                      checked={ack}
                      onChange={(e) => setAck(e.target.checked)}
                    />
                    我已审核以上最终版本、账号、范围、图片顺序、时间及授权窗口
                  </label>
                  <button
                    className="social-primary"
                    disabled={!ack}
                    onClick={() =>
                      void act(async () => {
                        await socialApi(
                          `/publish-schedules/${selected.id}/confirm`,
                          "POST",
                          {
                            approval_hash: selected.approval_hash,
                            acknowledge_risks: true,
                          },
                        );
                        await open(selected.id);
                      })
                    }
                  >
                    确认此版本定时发布
                  </button>
                </>
              )}
              {["uncertain", "submitted"].includes(selected.state) && (
                <p className="text-amber-700">
                  请前往对应草稿的“提交与核验记录”记录人工平台核验。本页面不提供重发操作。
                </p>
              )}
            </fieldset>
            <details open>
              <summary>状态变化与操作者记录</summary>
              {selected.events?.map((e, i) => (
                <div key={i}>
                  {new Date(e.created_at).toLocaleString()} ·{" "}
                  {publishStates[e.state] ?? e.state} · {e.reason} ·{" "}
                  {e.actor_id ? "操作者 " + e.actor_id : "后台系统"}
                  <details>
                    <summary>当时确认摘要与版本</summary>
                    <pre className="text-xs whitespace-pre-wrap break-all">
                      {JSON.stringify(e.snapshot, null, 2)}
                    </pre>
                  </details>
                </div>
              ))}
            </details>
            <details>
              <summary>提交回执与人工核验</summary>
              <pre className="whitespace-pre-wrap break-all text-xs">
                {JSON.stringify(
                  {
                    receipt: selected.receipt,
                    verification: selected.verification,
                  },
                  null,
                  2,
                )}
              </pre>
            </details>
          </div>
        )}
      </Modal>
    </section>
  );
}
