import { useEffect, useRef, useState } from "react";
import { request } from "../../services/api";
import {
  socialApi,
  type Draft,
  type Run,
  type Connection,
  type SkillOption,
  type SocialCard,
  type VisualTemplate,
} from "../../services/socialOps";
import Modal from "../common/Modal";
import {
  ScheduleFields,
  zonedTime,
  publishStates,
} from "./PublishScheduleControls";
import { WorkspaceImagePicker } from "../detail-page/WorkspaceImagePicker";

type Intent = {
  id: string;
  scheduled_at?: string;
  schedule_timezone?: string;
  authorized_until?: string;
  state: string;
  approval_hash: string;
  revision: number;
  visibility: string;
  error: string;
  receipt: unknown;
  verification?: unknown;
  payload: { display_name: string; account_id: string; data: Draft["data"] };
};
type Check = {
  id: string;
  revision: number;
  structural: {
    hard_errors: string[];
    warnings: string[];
    unknowns: string[];
    status: string;
  };
};
const stateNames: Record<string, string> = {
  ...publishStates,
  awaiting_confirmation: "等待最终确认",
  dispatching: "正在提交，请勿重复操作",
  submitted: "已提交待核验",
  verified: "已核验发布（人工）",
  uncertain: "结果不确定，禁止重发",
  not_published: "人工核验未发布（原记录保留）",
  failed: "失败",
  cancelled: "已取消",
  invalidated: "内容已变化，确认失效",
};
const initialVisual: VisualTemplate = {
  background: "#FFF8F0",
  foreground: "#30343B",
  accent: "#2563EB",
  watermark: "",
};

export function SocialAssetPreview({ id }: { id: string }) {
  const [src, setSrc] = useState(""),
    [open, setOpen] = useState(false);
  useEffect(() => {
    let active = true,
      object = "";
    void request<Blob>({
      url: `/social-ops/assets/${id}/preview`,
      responseType: "blob",
    })
      .then((blob) => {
        object = URL.createObjectURL(blob);
        if (active) setSrc(object);
      })
      .catch(() => {
        if (active) setSrc("");
      });
    return () => {
      active = false;
      if (object) URL.revokeObjectURL(object);
    };
  }, [id]);
  return src ? (
    <>
      <button
        type="button"
        aria-label="查看卡片大图"
        onClick={() => setOpen(true)}
        className="w-full"
      >
        <img src={src} alt="图文卡片" className="w-full rounded-xl border" />
        <span className="text-xs text-text-secondary">查看大图</span>
      </button>
      <Modal
        isOpen={open}
        onClose={() => setOpen(false)}
        title="图文卡片预览"
        maxWidth="max-w-3xl"
      >
        <img src={src} alt="完整图文卡片" className="w-full" />
      </Modal>
    </>
  ) : (
    <div className="aspect-[3/4] bg-surface flex items-center justify-center text-sm">
      图片加载中或无权访问
    </div>
  );
}

export default function CardStudio({
  draft,
  dirty,
  jobId,
  runs,
  accounts,
  skills,
  onRefresh,
}: {
  draft: Draft;
  dirty: boolean;
  jobId: string;
  runs: Run[];
  accounts: Connection[];
  skills: SkillOption[];
  onRefresh: () => Promise<void>;
}) {
  const [cards, updateCards] = useState<SocialCard[]>([]),
    [visual, updateVisual] = useState(initialVisual),
    [cardDirty, setCardDirty] = useState(false);
  const setCards = (value: SocialCard[]) => {
    updateCards(value);
    setCardDirty(true);
  };
  const setVisual = (value: VisualTemplate) => {
    updateVisual(value);
    setCardDirty(true);
  };
  const [pins, setPins] = useState<Record<string, string>>({}),
    [credits, setCredits] = useState(10);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [check, setCheck] = useState<Check | null>(null),
    [intents, setIntents] = useState<Intent[]>([]),
    [intent, setIntent] = useState<Intent | null>(null);
  const [account, setAccount] = useState(""),
    [visibility, setVisibility] = useState("private"),
    [ack, setAck] = useState(false);
  const [scheduledEnabled, setScheduledEnabled] = useState(false),
    [mode, setMode] = useState("immediate"),
    [scheduleTime, setScheduleTime] = useState(""),
    [scheduleZone, setScheduleZone] = useState("Asia/Shanghai"),
    [grace, setGrace] = useState(60);
  useEffect(() => {
    void socialApi<{ enabled: boolean }>("/publish-schedules/capabilities")
      .then((r) => setScheduledEnabled(r.enabled))
      .catch(() => {});
  }, []);
  const [picker, setPicker] = useState<number | null>(null),
    [source, setSource] = useState(""),
    [rights, setRights] = useState("");
  const [remoteId, setRemoteId] = useState(""),
    [remoteUrl, setRemoteUrl] = useState(""),
    [verifyNote, setVerifyNote] = useState("");
  const [recoveryNote, setRecoveryNote] = useState(""),
    [recoveryAck, setRecoveryAck] = useState(false);
  const requests = useRef<Record<string, string>>({});
  const key = (kind: string) => {
    const k = JSON.stringify([
      kind,
      draft.id,
      draft.current_revision,
      cards,
      visual,
      pins,
      credits,
    ]);
    return requests.current[k] ?? (requests.current[k] = crypto.randomUUID());
  };
  const refreshIntents = async () => {
    const rows = await socialApi<Intent[]>(
      `/contents/${draft.id}/publish-intents`,
    );
    setIntents(rows);
    setIntent((current) =>
      current ? (rows.find((row) => row.id === current.id) ?? current) : null,
    );
  };
  useEffect(() => {
    updateCards(
      draft.data.cards?.length
        ? draft.data.cards
        : [
            { role: "cover", headline: draft.data.title, body: "" },
            { role: "body", headline: "核心内容", body: draft.data.body },
            {
              role: "closing",
              headline: "回顾与下一步",
              body: "资料范围与未核实事项请见正文。",
            },
          ],
    );
    updateVisual(draft.data.visual ?? initialVisual);
    setCardDirty(false);
    setCheck(null);
    setIntent(null);
    setAck(false);
    setError("");
    let active = true;
    void socialApi<Intent[]>(`/contents/${draft.id}/publish-intents`)
      .then((rows) => {
        if (active) setIntents(rows);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [draft.id, draft.current_revision]);
  const act = async (fn: () => Promise<void>) => {
    if (busy || dirty) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作未完成，请刷新状态核对");
      try {
        const latest = await socialApi<Intent[]>(
          `/contents/${draft.id}/publish-intents`,
        );
        setIntents(latest);
        setIntent((current) =>
          current ? (latest.find((i) => i.id === current.id) ?? null) : null,
        );
      } catch {
        /* Preserve the error if status cannot be refreshed. */
      }
    } finally {
      setBusy(false);
    }
  };
  const save = async () => {
    await socialApi(`/contents/${draft.id}/cards`, "POST", {
      client_request_id: key("render"),
      expected_revision: draft.current_revision,
      cards,
      visual,
    });
    await onRefresh();
    setNotice("新图文版本已保存，请重新运行语义检查和整包检查。");
  };
  const produce = async () => {
    await socialApi("/production-runs", "POST", {
      client_request_id: key("ai-cards"),
      kind: "cards",
      job_id: jobId,
      content_id: draft.id,
      expected_revision: draft.current_revision,
      visual,
      stages: ["easel-card-design", "easel-card-xiaohongshu"].map(
        (skill_id) => ({ skill_id, revision: pins[skill_id] }),
      ),
      max_credits: credits,
    });
    await onRefresh();
    setNotice("已创建有预算上限的两阶段制作任务，不自动发布或重复调用。");
  };
  const swap = async (index: number, delta: number) => {
    const ids = draft.data.assets!.map((a) => a.id);
    [ids[index], ids[index + delta]] = [ids[index + delta], ids[index]];
    await socialApi(`/contents/${draft.id}/media`, "POST", {
      expected_revision: draft.current_revision,
      asset_ids: ids,
    });
    await onRefresh();
  };
  const semantic = runs.find(
    (r) =>
      r.kind === "check" &&
      r.status === "completed" &&
      (r.result as { content_id?: string; revision?: number } | null)
        ?.content_id === draft.id &&
      (r.result as { revision?: number } | null)?.revision ===
        draft.current_revision,
  );
  return (
    <section
      className="space-y-5 border-t pt-5"
      aria-label="图文制作与立即发布"
    >
      <div>
        <h3 className="font-semibold text-lg">图文制作</h3>
        <p className="text-sm text-text-secondary">
          固定品牌模板 · 1080×1440 · 保存的内容版本 v{draft.current_revision}
          。文字变化后图片需重新制作；修改素材后旧检查和确认失效。
        </p>
      </div>
      {dirty && (
        <p role="status" className="text-amber-700">
          请先保存文字。当前图片和检查属于之前保存的版本。
        </p>
      )}
      {error && (
        <p role="alert" className="text-red-700">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <fieldset disabled={busy || dirty} className="space-y-4">
        {runs
          .filter(
            (r) =>
              r.kind === "cards" &&
              r.status === "failed" &&
              (r.result as { manual_repair?: boolean } | null)?.manual_repair,
          )
          .map((r) => (
            <div key={r.id} className="border rounded-lg p-3">
              <p>
                卡片方案已保存，排版未通过：{r.error}。手动返工不再次调用模型。
              </p>
              <button
                onClick={() => {
                  const result = r.result as unknown as {
                    cards: SocialCard[];
                    visual: VisualTemplate;
                  };
                  setCards(result.cards);
                  setVisual(result.visual);
                }}
              >
                载入保存的卡片方案返工
              </button>
            </div>
          ))}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {(["background", "foreground", "accent"] as const).map((v, i) => (
            <label key={v} className="text-sm">
              {["背景色", "正文色", "品牌色"][i]}
              <input
                type="color"
                aria-label={["背景色", "正文色", "品牌色"][i]}
                value={visual[v]}
                onChange={(e) => setVisual({ ...visual, [v]: e.target.value })}
                className="block w-full"
              />
            </label>
          ))}
          <label className="text-sm">
            品牌水印
            <input
              aria-label="品牌水印"
              maxLength={40}
              value={visual.watermark}
              onChange={(e) =>
                setVisual({ ...visual, watermark: e.target.value })
              }
              className="border rounded-lg p-2 w-full"
            />
          </label>
        </div>
        <details>
          <summary>AI 卡片方法与预算</summary>
          <p className="text-sm">
            先审核并发布两个新
            Skill；每阶段固定一个版本，无工具权限。手动排版不调用模型。
          </p>
          {["easel-card-design", "easel-card-xiaohongshu"].map((id) => (
            <label key={id} className="block text-sm mt-2">
              {id}
              <select
                aria-label={id}
                value={pins[id] ?? ""}
                onChange={(e) => setPins({ ...pins, [id]: e.target.value })}
                className="border rounded-lg p-2 w-full"
              >
                <option value="">选择审核版本</option>
                {skills
                  .filter((s) => s.skill_id === id)
                  .map((s) => (
                    <option key={s.revision} value={s.revision}>
                      {s.name} · {s.revision}
                    </option>
                  ))}
              </select>
            </label>
          ))}
          <label>
            本次积分上限
            <input
              aria-label="卡片积分上限"
              type="number"
              min={2}
              max={1000}
              value={credits}
              onChange={(e) => setCredits(Number(e.target.value))}
            />
          </label>
          <button
            className="social-primary mt-3"
            disabled={
              !pins["easel-card-design"] || !pins["easel-card-xiaohongshu"]
            }
            onClick={() => void act(produce)}
          >
            AI 设计并制作卡片
          </button>
        </details>
        <div className="grid sm:grid-cols-2 gap-3">
          {cards.map((card, i) => (
            <div key={i} className="rounded-xl border p-4 space-y-2">
              <label className="text-sm">
                第{i + 1}张卡片类型
                <select
                  aria-label={`卡片${i + 1}类型`}
                  value={card.role}
                  onChange={(e) =>
                    setCards(
                      cards.map((c, n) =>
                        n === i
                          ? { ...c, role: e.target.value as SocialCard["role"] }
                          : c,
                      ),
                    )
                  }
                  className="border rounded-lg p-2 w-full"
                >
                  <option value="cover">封面</option>
                  <option value="body">正文</option>
                  <option value="closing">收尾</option>
                </select>
              </label>
              <input
                aria-label={`卡片${i + 1}标题`}
                maxLength={100}
                value={card.headline}
                onChange={(e) =>
                  setCards(
                    cards.map((c, n) =>
                      n === i ? { ...c, headline: e.target.value } : c,
                    ),
                  )
                }
                className="border rounded-lg p-2 w-full"
              />
              <textarea
                aria-label={`卡片${i + 1}正文`}
                maxLength={1500}
                value={card.body}
                onChange={(e) =>
                  setCards(
                    cards.map((c, n) =>
                      n === i ? { ...c, body: e.target.value } : c,
                    ),
                  )
                }
                className="border rounded-lg p-2 w-full min-h-32"
              />
              {cards.length > 2 && (
                <button
                  onClick={() => setCards(cards.filter((_, n) => n !== i))}
                >
                  移除此卡
                </button>
              )}
            </div>
          ))}
        </div>
        <div className="flex gap-3">
          <button
            disabled={cards.length >= 9}
            onClick={() =>
              setCards([
                ...cards.slice(0, -1),
                { role: "body", headline: "新观点", body: "" },
                cards[cards.length - 1],
              ])
            }
          >
            添加正文卡片
          </button>
          <button className="social-primary" onClick={() => void act(save)}>
            制作并保存卡片新版本
          </button>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
          {draft.data.assets?.map((a, i) => (
            <div key={a.id} className="space-y-2">
              <SocialAssetPreview id={a.id} />
              <p className="text-xs break-all">
                第{i + 1}张 · {a.width}×{a.height}
                <br />
                来源：{a.source}
                <br />
                权属说明：{a.rights}
              </p>
              <div className="flex gap-2 text-sm">
                <button
                  disabled={i === 0}
                  onClick={() => void act(() => swap(i, -1))}
                >
                  前移
                </button>
                <button
                  disabled={i === draft.data.assets!.length - 1}
                  onClick={() => void act(() => swap(i, 1))}
                >
                  后移
                </button>
                <button onClick={() => setPicker(i)}>替换素材</button>
              </div>
            </div>
          ))}
        </div>
        {!draft.data.assets?.length && (
          <p className="text-amber-700">
            待补素材：请制作卡片或从工作区选择已授权图片。
          </p>
        )}
        <div className="grid sm:grid-cols-2 gap-3">
          <label>
            素材来源说明
            <input
              aria-label="素材来源说明"
              value={source}
              onChange={(e) => setSource(e.target.value)}
              className="border rounded-lg p-2 w-full"
            />
          </label>
          <label>
            素材权属说明
            <input
              aria-label="素材权属说明"
              value={rights}
              onChange={(e) => setRights(e.target.value)}
              className="border rounded-lg p-2 w-full"
            />
          </label>
        </div>
        <button
          disabled={
            !source.trim() ||
            !rights.trim() ||
            (draft.data.assets?.length ?? 0) >= 9
          }
          onClick={() => setPicker(draft.data.assets?.length ?? 0)}
        >
          从工作区添加图片
        </button>
        {cardDirty && (
          <p className="text-amber-700">
            卡片或品牌模板有未保存修改，请先制作保存，检查和确认暂不可用。
          </p>
        )}
        <h3 className="font-semibold">检查完整图文包</h3>
        <p className="text-sm">
          先用上方“检查已保存版本”运行两阶段语义检查，再检查结构、尺寸、摘要与来源。模型不验证真实图片版权或平台审核。
        </p>
        <button
          className="social-primary"
          disabled={!semantic || cardDirty}
          onClick={() =>
            void act(async () =>
              setCheck(
                await socialApi<Check>(
                  `/contents/${draft.id}/package-checks`,
                  "POST",
                  {
                    expected_revision: draft.current_revision,
                    semantic_run_id: semantic!.id,
                  },
                ),
              ),
            )
          }
        >
          检查当前整包
        </button>
        {check && (
          <div className="rounded-lg border p-3">
            <strong>
              {check.structural.status === "blocked"
                ? "检查未通过"
                : "可进入最终确认"}
            </strong>
            {check.structural.hard_errors.map((e) => (
              <p key={e} className="text-red-700">
                {e}
              </p>
            ))}
            {[...check.structural.warnings, ...check.structural.unknowns].map(
              (e) => (
                <p key={e} className="text-sm">
                  {e}
                </p>
              ),
            )}
          </div>
        )}
        <h3 className="font-semibold">最终审核与发布</h3>
        <label>
          发布方式
          <select
            aria-label="发布方式"
            value={mode}
            onChange={(e) => {
              setMode(e.target.value);
              setIntent(null);
              setAck(false);
            }}
            className="border rounded-lg p-2 w-full"
          >
            <option value="immediate">确认后立即发布</option>
            <option value="scheduled" disabled={!scheduledEnabled}>
              审核最终版本后定时发布{!scheduledEnabled ? "（尚未启用）" : ""}
            </option>
          </select>
        </label>
        {mode === "scheduled" && (
          <ScheduleFields
            time={scheduleTime}
            zone={scheduleZone}
            grace={grace}
            onChange={(t, z, g) => {
              setScheduleTime(t);
              setScheduleZone(z);
              setGrace(g);
              setIntent(null);
              setAck(false);
            }}
          />
        )}
        <p className="text-sm">
          仅提交逐条审核的最终版本；定时发布超过授权窗口不补发，结果不确定时只核验。
        </p>
        <label>
          发布账号
          <select
            aria-label="发布账号"
            value={account}
            onChange={(e) => {
              setAccount(e.target.value);
              setIntent(null);
              setAck(false);
            }}
            className="border rounded-lg p-2 w-full"
          >
            <option value="">选择账号</option>
            {accounts
              .filter((a) => a.platform === "xiaohongshu")
              .map((a) => (
                <option key={a.id} value={a.id}>
                  {a.display_name} · {a.login_state}
                </option>
              ))}
          </select>
        </label>
        <label>
          可见范围
          <select
            aria-label="发布可见范围"
            value={visibility}
            onChange={(e) => {
              setVisibility(e.target.value);
              setIntent(null);
              setAck(false);
            }}
            className="border rounded-lg p-2 w-full"
          >
            <option value="private">仅自己可见</option>
            <option value="public">公开可见</option>
          </select>
        </label>
        <button
          disabled={
            cardDirty ||
            !check ||
            check.structural.hard_errors.length > 0 ||
            !account
          }
          onClick={() =>
            void act(async () => {
              setIntent(
                await socialApi<Intent>(
                  mode === "scheduled"
                    ? "/publish-schedules"
                    : "/publish-intents",
                  "POST",
                  {
                    content_id: draft.id,
                    expected_revision: draft.current_revision,
                    connection_id: account,
                    check_id: check!.id,
                    visibility,
                    ...(mode === "scheduled"
                      ? {
                          scheduled_at: zonedTime(scheduleTime, scheduleZone),
                          timezone: scheduleZone,
                          grace_seconds: grace,
                        }
                      : {}),
                  },
                ),
              );
              setAck(false);
              await refreshIntents();
            })
          }
        >
          查看最终发布确认
        </button>
        {intent && (
          <div
            className="border rounded-xl p-4 space-y-3"
            role="region"
            aria-label="最终发布确认"
          >
            <strong>{stateNames[intent.state]}</strong>
            <p>
              版本 v{intent.revision} · {intent.payload.display_name} ·{" "}
              {intent.visibility === "public" ? "公开可见" : "仅自己可见"}
            </p>
            {intent.scheduled_at && (
              <p>
                定时：
                {new Date(intent.scheduled_at).toLocaleString("zh-CN", {
                  timeZone: intent.schedule_timezone,
                })}{" "}
                ({intent.schedule_timezone}) · 授权截止{" "}
                {new Date(intent.authorized_until!).toLocaleString("zh-CN", {
                  timeZone: intent.schedule_timezone,
                })}
                ，过期不补发。
              </p>
            )}
            <h4>{intent.payload.data.title}</h4>
            <p className="whitespace-pre-wrap">{intent.payload.data.body}</p>
            <p>{intent.payload.data.tags.map((t) => "#" + t).join(" ")}</p>
            <div className="grid grid-cols-3 gap-2">
              {intent.payload.data.assets?.map((a) => (
                <SocialAssetPreview key={a.id} id={a.id} />
              ))}
            </div>
            <p>
              {intent.payload.data.assets?.length}
              张图片，绑定素材摘要与账号凭据版本。
            </p>
            <label>
              <input
                type="checkbox"
                checked={ack}
                onChange={(e) => setAck(e.target.checked)}
              />
              我已检查最终文字、图片、来源与权属，知悉提交不等于已公开发布。
            </label>
            <button
              className="social-primary"
              disabled={
                cardDirty ||
                !ack ||
                intent.state !== "awaiting_confirmation" ||
                intent.revision !== draft.current_revision
              }
              onClick={() =>
                void act(async () => {
                  const saved = await socialApi<Intent>(
                    `${intent.scheduled_at ? "/publish-schedules" : "/publish-intents"}/${intent.id}/confirm`,
                    "POST",
                    {
                      approval_hash: intent.approval_hash,
                      acknowledge_risks: true,
                    },
                  );
                  setIntent(saved);
                  await refreshIntents();
                })
              }
            >
              {intent.scheduled_at
                ? "确认最终版本并定时发布"
                : "确认最终版本并立即发布"}
            </button>
          </div>
        )}
        <div className="flex justify-between">
          <h3 className="font-semibold">提交与核验记录</h3>
          <button onClick={() => void act(refreshIntents)}>刷新发布状态</button>
        </div>
        {intents.map((item) => (
          <div key={item.id} className="border rounded-xl p-3 space-y-2">
            <strong>{stateNames[item.state] ?? item.state}</strong>
            <p className="text-sm">
              v{item.revision} · {item.payload.display_name} ·{" "}
              {item.visibility === "public" ? "公开可见" : "仅自己可见"}
            </p>
            {item.error && <p>{item.error}</p>}
            <details>
              <summary>提交回执与核验依据</summary>
              <pre className="text-xs whitespace-pre-wrap break-all">
                {JSON.stringify(
                  { receipt: item.receipt, verification: item.verification },
                  null,
                  2,
                )}
              </pre>
            </details>
            {[
              "awaiting_confirmation",
              "scheduled",
              "waiting_budget",
              "paused",
              "preparing",
              "invalidated",
            ].includes(item.state) && (
              <button
                onClick={() =>
                  void act(async () => {
                    await socialApi(
                      `/publish-intents/${item.id}/cancel`,
                      "POST",
                    );
                    setIntent(null);
                    await refreshIntents();
                  })
                }
              >
                取消此意图
              </button>
            )}
            {item.state === "uncertain" && !item.scheduled_at && (
              <details>
                <summary>人工核验未发布，准备重新确认</summary>
                <p>
                  仅限原操作者：确认目标账号、全部笔记、审核中及未通过记录均无本次笔记，并结合提交日志判断。仅未找到搜索结果不足以核验。仍在执行或已有提交依据时不可恢复。
                </p>
                <textarea
                  aria-label="未发布核验依据"
                  className="border p-2 w-full"
                  value={recoveryNote}
                  onChange={(e) => setRecoveryNote(e.target.value)}
                />
                <label>
                  <input
                    type="checkbox"
                    checked={recoveryAck}
                    onChange={(e) => setRecoveryAck(e.target.checked)}
                  />
                  我已核对目标账号及全部状态，确认本次未发布；知悉原记录保留，必须重新检查和确认，不会自动重发。
                </label>
                <button
                  disabled={!recoveryAck || recoveryNote.trim().length < 30}
                  onClick={() =>
                    void act(async () => {
                      await socialApi(
                        `/publish-intents/${item.id}/resolve-not-published`,
                        "POST",
                        {
                          note: recoveryNote.trim(),
                          account_checked: true,
                          all_statuses_checked: true,
                          acknowledge_new_confirmation: true,
                        },
                      );
                      setIntent(null);
                      setCheck(null);
                      setAck(false);
                      setRecoveryAck(false);
                      setRecoveryNote("");
                      setNotice(
                        "已保留未发布核验记录。请重新检查整包，再查看最终发布确认。",
                      );
                      await refreshIntents();
                    })
                  }
                >
                  保存未发布核验（不会重发）
                </button>
              </details>
            )}
            {["submitted", "uncertain"].includes(item.state) && (
              <details>
                <summary>记录人工平台核验（不会重发）</summary>
                <label>
                  帖子ID
                  <input
                    aria-label="核验帖子ID"
                    value={remoteId}
                    onChange={(e) => setRemoteId(e.target.value)}
                    className="border p-2 w-full"
                  />
                </label>
                <label>
                  帖子链接
                  <input
                    aria-label="核验帖子链接"
                    value={remoteUrl}
                    onChange={(e) => setRemoteUrl(e.target.value)}
                    className="border p-2 w-full"
                  />
                </label>
                <label>
                  核验依据
                  <textarea
                    aria-label="人工核验依据"
                    value={verifyNote}
                    onChange={(e) => setVerifyNote(e.target.value)}
                    className="border p-2 w-full"
                  />
                </label>
                <p className="text-sm">
                  请在目标账号实际查看对应笔记及可见状态，再填写核验依据。此记录明确标为人工核验。
                </p>
                <button
                  disabled={!remoteId || !remoteUrl || verifyNote.length < 10}
                  onClick={() =>
                    void act(async () => {
                      await socialApi(
                        `/publish-intents/${item.id}/verify`,
                        "POST",
                        {
                          remote_id: remoteId,
                          url: remoteUrl,
                          note: verifyNote,
                        },
                      );
                      await refreshIntents();
                    })
                  }
                >
                  保存人工核验记录
                </button>
              </details>
            )}
          </div>
        ))}
      </fieldset>
      <WorkspaceImagePicker
        open={picker !== null}
        remaining={1}
        onClose={() => setPicker(null)}
        onSelect={(paths) => {
          const position = picker!;
          void act(async () => {
            const ref = await socialApi<{ resource_ref: string }>(
              "/media-reference",
              "POST",
              { path: paths[0] },
            );
            await socialApi(`/contents/${draft.id}/media/import`, "POST", {
              expected_revision: draft.current_revision,
              position,
              resource_ref: ref.resource_ref,
              source,
              rights,
            });
            await onRefresh();
          });
        }}
      />
    </section>
  );
}
