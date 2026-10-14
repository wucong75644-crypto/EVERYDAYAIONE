import { useEffect, useState } from "react";
import {
  socialApi,
  type Connection,
  type Profile,
  type SkillOption,
} from "../../services/socialOps";
import Modal from "../common/Modal";
import type {
  Snapshot,
  Published,
  Evidence,
  Conclusion,
  Stats,
  Review,
  Preview,
  Schedule,
} from "../../services/socialAnalytics";
const labels: Record<string, string> = {
  likes: "点赞",
  comments: "评论",
  saves: "收藏",
  shares: "分享",
  views: "阅读/播放",
  impressions: "曝光",
  conversions: "转化",
  followers: "新增粉丝（导入）",
  completion_rate: "完播率（导入，0~1）",
};
const kinds: Record<string, string> = {
  postmortem: "内容复盘",
  comment_insights: "评论洞察",
  strategy: "策略建议",
};
const skillKeys: Record<string, string> = {
  postmortem: "easel-content-postmortem",
  comment_insights: "easel-comment-insights",
  strategy: "easel-strategy-advisor",
};
export const metricText = (v: number | null | undefined) =>
  v == null ? "缺失" : String(v);
const isoDay = (day: string, end = false) =>
  new Date(`${day}T${end ? "23:59:59.999" : "00:00:00"}+08:00`).toISOString();
const json = (v: unknown) => (
  <pre className="whitespace-pre-wrap break-all text-xs">
    {JSON.stringify(v, null, 2)}
  </pre>
);
export default function AnalyticsPanel({
  accounts,
  profiles,
  onChanged,
  onJobSelected,
}: {
  accounts: Connection[];
  profiles: Profile[];
  onChanged?: () => Promise<void>;
  onJobSelected?: (id: string) => void;
}) {
  const [open, setOpen] = useState(false),
    [rows, setRows] = useState<Published[]>([]),
    [selected, setSelected] = useState<string[]>([]);
  const [account, setAccount] = useState(""),
    [profile, setProfile] = useState(""),
    [format, setFormat] = useState("");
  const [start, setStart] = useState(""),
    [end, setEnd] = useState("");
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false),
    [caps, setCaps] = useState<unknown>(null);
  const [history, setHistory] = useState<Snapshot[] | null>(null),
    [stats, setStats] = useState<Stats | null>(null),
    [evidence, setEvidence] = useState<unknown>(null);
  const [collect, setCollect] = useState<Published | null>(null),
    [url, setUrl] = useState(""),
    [pages, setPages] = useState(0),
    [publishedAt, setPublishedAt] = useState(""),
    [contentType, setContentType] = useState("image");
  const [file, setFile] = useState<File | null>(null),
    [source, setSource] = useState(""),
    [mapping, setMapping] = useState("{}"),
    [preview, setPreview] = useState<Preview | null>(null);
  const [options, setOptions] = useState<SkillOption[]>([]),
    [kind, setKind] = useState("postmortem"),
    [pin, setPin] = useState(""),
    [credits, setCredits] = useState(10);
  const [reports, setReports] = useState<Review[]>([]),
    [report, setReport] = useState<Review | null>(null),
    [edits, setEdits] = useState<Record<string, string>>({});
  const [periodic, setPeriodic] = useState(false),
    [interval, setInterval] = useState(24),
    [frequency, setFrequency] = useState("none"),
    [schedules, setSchedules] = useState<Schedule[]>([]);
  const act = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  };
  const reload = async () => {
    const q = new URLSearchParams();
    if (account) q.set("connection_id", account);
    if (profile) q.set("profile_id", profile);
    if (format) q.set("content_type", format);
    if (start) q.set("start", isoDay(start));
    if (end) q.set("end", isoDay(end, true));
    const [data, list, plans] = await Promise.all([
      socialApi<Published[]>(`/analytics/contents?${q}`),
      socialApi<Review[]>("/analytics/reviews"),
      socialApi<Schedule[]>("/analytics/schedules"),
    ]);
    setRows(data);
    setReports(list);
    setSchedules(plans);
    setSelected((v) => v.filter((id) => data.some((r) => r.id === id)));
  };
  useEffect(() => {
    if (!open) return;
    void act(async () => {
      const [c, s] = await Promise.all([
        socialApi<unknown>("/analytics/capabilities"),
        socialApi<SkillOption[]>("/skill-options"),
      ]);
      setCaps(c);
      setOptions(s);
      await reload();
    });
  }, [open, account, profile, format, start, end]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!open) return;
    const timer = window.setInterval(() => {
      void reload().catch(() => {});
    }, 15000);
    return () => window.clearInterval(timer);
  }, [open, account, profile, format, start, end]); // eslint-disable-line react-hooks/exhaustive-deps
  const refs = (items: Evidence[]) =>
    items.map((e, i) => (
      <button
        key={i}
        onClick={() =>
          setEvidence({
            reference: e,
            source: report?.input.sources.find((s) => s.id === e.snapshot_id),
          })
        }
      >
        查看证据 {i + 1}
      </button>
    ));
  const conclusions = (items: Conclusion[]) =>
    items.map((c, i) => (
      <li key={i}>
        {c.category && `${c.category}：`}
        {c.text} {refs(c.evidence)}
      </li>
    ));
  const collectionData = () => ({
    client_request_id: crypto.randomUUID(),
    url,
    comment_pages: pages,
    published_at: publishedAt
      ? new Date(publishedAt + ":00+08:00").toISOString()
      : null,
    content_type: contentType,
  });
  const scheduleData = (enabled: boolean) => ({
    intent_id: collect!.id,
    collection: collectionData(),
    enabled,
    interval_hours: interval,
    review_frequency: enabled ? frequency : "none",
    review_stage:
      enabled && frequency !== "none"
        ? { skill_id: skillKeys.postmortem, revision: pin }
        : null,
    max_credits: credits,
  });
  return (
    <section className="social-analytics social-panel mt-4 p-4">
      <button
        className="social-button-secondary"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        数据回收、内容复盘与策略建议 {open ? "收起" : "展开"}
      </button>
      {open && (
        <div className="space-y-4 mt-4">
          {error && (
            <p role="alert" className="text-red-700">
              {error}
            </p>
          )}
          {notice && <p role="status">{notice}</p>}
          <p>
            数据关联原发布版本。后台指标需导入，缺失不补零。日期筛选是采集时间（Asia/Shanghai）。
          </p>
          <details>
            <summary>平台指标能力与限制</summary>
            {json(caps)}
          </details>
          <div className="flex flex-wrap gap-3">
            <label>
              账号
              <select
                value={account}
                onChange={(e) => setAccount(e.target.value)}
              >
                <option value="">全部账号</option>
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.display_name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              画像
              <select
                value={profile}
                onChange={(e) => setProfile(e.target.value)}
              >
                <option value="">全部画像</option>
                {profiles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.data.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              内容类型
              <select
                value={format}
                onChange={(e) => setFormat(e.target.value)}
              >
                <option value="">全部类型</option>
                <option value="image">图文</option>
                <option value="video">视频</option>
                <option value="text">文字</option>
              </select>
            </label>
            <label>
              起始日期
              <input
                type="date"
                value={start}
                onChange={(e) => setStart(e.target.value)}
              />
            </label>
            <label>
              结束日期
              <input
                type="date"
                value={end}
                onChange={(e) => setEnd(e.target.value)}
              />
            </label>
            <button disabled={busy} onClick={() => void act(reload)}>
              刷新
            </button>
          </div>
          {!rows.length && (
            <p>
              当前筛选没有内容。可清空日期查看未采集内容；无可信平台ID请先完成发布核验。
            </p>
          )}
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th>内容及关联标识</th>
                  <th>采集状态</th>
                  {Object.keys(labels).map((k) => (
                    <th key={k}>{labels[k]}</th>
                  ))}
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <label>
                        <input
                          type="checkbox"
                          aria-label={`选择${r.title}`}
                          checked={selected.includes(r.id)}
                          disabled={r.state !== "verified"}
                          onChange={(e) =>
                            setSelected(
                              e.target.checked
                                ? [...selected, r.id]
                                : selected.filter((id) => id !== r.id),
                            )
                          }
                        />
                        {r.title} v{r.revision}
                      </label>
                      <details>
                        <summary>导入关联标识</summary>
                        {json({
                          intent_id: r.id,
                          account_id: r.account_id,
                          remote_id: r.verification?.remote_id,
                        })}
                      </details>
                    </td>
                    <td>
                      {r.state !== "verified"
                        ? "请先核验平台ID"
                        : r.collection &&
                            [
                              "needs_input",
                              "paused_auth",
                              "paused_platform",
                              "failed",
                            ].includes(r.collection.status)
                          ? `采集失败/暂停：${r.collection.last_error}`
                          : r.collection &&
                              ["queued", "running", "waiting_budget"].includes(
                                r.collection.status,
                              )
                            ? `采集中：${r.collection.status}`
                            : r.latest
                              ? Object.values(r.latest.metrics).some(
                                  (v) => v != null,
                                )
                                ? "已采集"
                                : "数据不足（指标全部缺失）"
                              : "未采集"}
                      {r.latest && (
                        <p>{new Date(r.latest.observed_at).toLocaleString()}</p>
                      )}
                    </td>
                    {Object.keys(labels).map((k) => (
                      <td key={k}>{metricText(r.latest?.metrics[k])}</td>
                    ))}
                    <td>
                      <button
                        disabled={busy || r.state !== "verified"}
                        onClick={() => {
                          setCollect(r);
                          setUrl("");
                          setPeriodic(false);
                        }}
                      >
                        回收/周期
                      </button>
                      <button
                        disabled={busy || !r.latest}
                        onClick={() =>
                          void act(async () =>
                            setHistory(
                              await socialApi<Snapshot[]>(
                                `/analytics/contents/${r.id}/snapshots`,
                              ),
                            ),
                          )
                        }
                      >
                        指标变化与来源
                      </button>
                      {r.collection && (
                        <button
                          onClick={() => onJobSelected?.(r.collection!.id)}
                        >
                          查看采集任务与恢复
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button
            disabled={busy || !selected.length}
            onClick={() =>
              void act(async () => {
                const q = new URLSearchParams();
                selected.forEach((i) => q.append("intent_ids", i));
                if (start) q.set("start", isoDay(start));
                if (end) q.set("end", isoDay(end, true));
                setStats(await socialApi<Stats>(`/analytics/statistics?${q}`));
              })
            }
          >
            比较所选内容（程序统计）
          </button>
          {stats && (
            <div>
              <p>
                样本 {stats.sample_size} 条，快照 {stats.snapshot_count} 条
              </p>
              {json(stats.observed_range)}
              <p>
                {Object.entries(stats.coverage)
                  .map(([k, v]) => `${labels[k]}覆盖 ${v.available}/${v.total}`)
                  .join("；")}
              </p>
              {stats.groups.map((g, i) => (
                <p key={i}>
                  {g.platform} / {g.content_type} / 发布年龄 {g.age_bucket} /{" "}
                  {g.scope}：{g.sample_size} 条，
                  {g.insufficient ? "数据不足，仅描述样本" : "同覆盖分组"}
                  ；中位数 {JSON.stringify(g.medians)}
                </p>
              ))}
              {stats.contents.map((c) => (
                <p key={c.snapshot_id}>
                  快照 {c.snapshot_id}：互动率{" "}
                  {c.engagement_rate == null
                    ? "数据不足"
                    : `${(c.engagement_rate * 100).toFixed(2)}%`}{" "}
                  {c.formula || c.rate_missing_reason}
                </p>
              ))}
              <p>{stats.limitations.join("；")}</p>
            </div>
          )}
          <details>
            <summary>CSV / Excel 导入（先预览后确认）</summary>
            <p>
              必填：intent_id、account_id、remote_id、observed_at（带时区）、content_type（image/video/text）、scope（creator_cumulative/public_cumulative）。可选published_at、denominator（空/views/impressions）及指标。计数为精确非负整数，completion_rate是导入原始0至1比例（不带百分号）；空白为缺失，0为真实零。Excel仅单表xlsx，无公式。重复复用，冲突拒绝。
            </p>
            <button
              onClick={() =>
                void act(async () => {
                  const text = await socialApi<string>(
                    "/analytics/imports/template",
                  );
                  const ref = URL.createObjectURL(
                    new Blob([text], { type: "text/csv;charset=utf-8" }),
                  );
                  const a = document.createElement("a");
                  a.href = ref;
                  a.download = "social-metrics.csv";
                  a.click();
                  URL.revokeObjectURL(ref);
                })
              }
            >
              下载模板
            </button>
            <label>
              文件
              <input
                type="file"
                accept=".csv,.xlsx"
                onChange={(e) => {
                  setFile(e.target.files?.[0] || null);
                  setPreview(null);
                }}
              />
            </label>
            <label>
              来源说明
              <input
                value={source}
                onChange={(e) => {
                  setSource(e.target.value);
                  setPreview(null);
                }}
              />
            </label>
            <label>
              字段映射（标准字段:文件列名，默认同名）
              <textarea
                value={mapping}
                onChange={(e) => {
                  setMapping(e.target.value);
                  setPreview(null);
                }}
              />
            </label>
            <button
              disabled={busy || !file || !source.trim()}
              onClick={() =>
                void act(async () => {
                  const form = new FormData();
                  form.append("file", file!);
                  form.append("source", source);
                  form.append("mapping", mapping);
                  setPreview(
                    await socialApi<Preview>(
                      "/analytics/imports/preview",
                      "POST",
                      form,
                    ),
                  );
                })
              }
            >
              预览导入
            </button>
            {preview && (
              <div>
                <p>
                  {preview.rules}；重复 {preview.duplicate_count} 条
                </p>
                {preview.rows.map((r) => (
                  <p key={r.line}>
                    行 {r.line}：{r.data.remote_id} /{" "}
                    {JSON.stringify(r.data.metrics)} —{" "}
                    {r.errors.length
                      ? r.errors.join("；")
                      : r.duplicate
                        ? "复用已有快照"
                        : "可导入"}
                  </p>
                ))}
                <button
                  disabled={busy || !preview.valid}
                  onClick={() =>
                    void act(async () => {
                      const result = await socialApi<{
                        created: number;
                        reused: number;
                      }>(`/analytics/imports/${preview.id}/confirm`, "POST", {
                        preview_hash: preview.preview_hash,
                      });
                      setNotice(
                        `导入完成：新建${result.created}，复用${result.reused}`,
                      );
                      setPreview(null);
                      await reload();
                    })
                  }
                >
                  确认当前预览
                </button>
              </div>
            )}
          </details>
          <div className="flex flex-wrap gap-3">
            <label>
              报告类型
              <select
                value={kind}
                onChange={(e) => {
                  setKind(e.target.value);
                  setPin("");
                }}
              >
                {Object.entries(kinds).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
            <label>
              固定审核Skill版本
              <select value={pin} onChange={(e) => setPin(e.target.value)}>
                <option value="">选择已审核版本</option>
                {options
                  .filter((s) => s.skill_id === skillKeys[kind])
                  .map((s) => (
                    <option key={s.revision} value={s.revision}>
                      {s.name} {s.revision}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              本次积分上限
              <input
                type="number"
                min={1}
                max={100}
                value={credits}
                onChange={(e) => setCredits(Number(e.target.value))}
              />
            </label>
            <button
              disabled={busy || !selected.length || !pin || !start || !end}
              onClick={() =>
                void act(async () => {
                  const result = await socialApi<{
                    reused: boolean;
                  }>("/analytics/reviews", "POST", {
                    client_request_id: crypto.randomUUID(),
                    intent_ids: selected,
                    start: isoDay(start),
                    end: isoDay(end, true),
                    kind,
                    stages: [{ skill_id: skillKeys[kind], revision: pin }],
                    max_credits: credits,
                  });
                  setNotice(
                    result.reused
                      ? "输入未变化，已复用原报告/运行，没有新建模型分析。"
                      : "报告生成中，输入、画像和Skill版本已固定。",
                  );
                  await reload();
                })
              }
            >
              生成所选内容报告
            </button>
          </div>
          <p>
            生成报告前请选择时间范围。策略需先完成内容复盘；评论分析需保存评论样本。中断按原额度人工恢复，无自动模型重试。
          </p>
          {reports.map((r) => (
            <div key={r.id}>
              <button
                onClick={() =>
                  void act(async () =>
                    setReport(
                      await socialApi<Review>(`/analytics/reviews/${r.id}`),
                    ),
                  )
                }
              >
                {kinds[r.kind]}：
                {["queued", "running"].includes(r.status)
                  ? "报告生成中"
                  : r.status}{" "}
                {r.error}
              </button>
              {r.status === "needs_input" && (
                <button
                  disabled={busy}
                  onClick={() =>
                    void act(async () => {
                      await socialApi(
                        `/production-runs/${r.id}/resume`,
                        "POST",
                      );
                      await reload();
                    })
                  }
                >
                  按原预算继续剩余阶段
                </button>
              )}
            </div>
          ))}
          {!!schedules.length && (
            <details>
              <summary>周期配置</summary>
              {schedules.map((s) => (
                <p key={s.id}>
                  {s.intent_id}：{s.enabled ? "启用" : "暂停"}；下次{" "}
                  {new Date(s.next_at).toLocaleString()}；{s.error}
                  {s.enabled && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        void act(async () => {
                          await socialApi(
                            `/analytics/schedules/${s.id}/pause`,
                            "POST",
                          );
                          await reload();
                          setNotice(
                            "周期已停止，已发出的有界调用可能仍在完成。",
                          );
                        })
                      }
                    >
                      停止周期
                    </button>
                  )}
                </p>
              ))}
            </details>
          )}
        </div>
      )}
      {collect && (
        <Modal
          isOpen
          title="回收真实数据 / 配置周期"
          onClose={() => setCollect(null)}
        >
          <div className="social-analytics-form space-y-3">
            <p>
              {collect.title} v{collect.revision}；平台ID{" "}
              {collect.verification?.remote_id}。返回作者ID须匹配连接账号。
            </p>
            <label>
              完整采集链接（含xsec_token）
              <input
                className="w-full"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
            </label>
            <label>
              内容形式
              <select
                value={contentType}
                onChange={(e) => setContentType(e.target.value)}
              >
                <option value="image">图文</option>
                <option value="video">视频</option>
                <option value="text">文字</option>
              </select>
            </label>
            <label>
              发布时间（可空，Asia/Shanghai）
              <input
                type="datetime-local"
                value={publishedAt}
                onChange={(e) => setPublishedAt(e.target.value)}
              />
            </label>
            <label>
              评论页数
              <input
                type="number"
                min={0}
                max={3}
                value={pages}
                onChange={(e) => setPages(Number(e.target.value))}
              />
            </label>
            <p>
              每页最多十条及已展开回复，不代表全部评论；读取受原账号预算限制。
            </p>
            <label>
              <input
                type="checkbox"
                checked={periodic}
                onChange={(e) => setPeriodic(e.target.checked)}
              />
              配置周期回收
            </label>
            {periodic && (
              <>
                <label>
                  间隔小时
                  <input
                    type="number"
                    min={1}
                    max={720}
                    value={interval}
                    onChange={(e) => setInterval(Number(e.target.value))}
                  />
                </label>
                <label>
                  复盘周期
                  <select
                    value={frequency}
                    onChange={(e) => setFrequency(e.target.value)}
                  >
                    <option value="none">仅回收</option>
                    <option value="weekly">周度</option>
                    <option value="monthly">月度</option>
                  </select>
                </label>
                <p>
                  使用上方内容复盘Skill及积分上限；无变化复用，平台异常暂停。
                </p>
              </>
            )}
            <button
              disabled={
                busy ||
                !url ||
                (periodic &&
                  frequency !== "none" &&
                  (kind !== "postmortem" || !pin))
              }
              onClick={() =>
                void act(async () => {
                  if (periodic)
                    await socialApi(
                      "/analytics/schedules",
                      "PUT",
                      scheduleData(true),
                    );
                  else
                    await socialApi(
                      `/analytics/contents/${collect.id}/collect`,
                      "POST",
                      collectionData(),
                    );
                  setCollect(null);
                  setNotice(periodic ? "周期已保存。" : "指标回收已排队。");
                  await reload();
                })
              }
            >
              {periodic ? "保存并启用周期" : "开始一次回收"}
            </button>
            <button
              disabled={busy || !url}
              onClick={() =>
                void act(async () => {
                  await socialApi(
                    "/analytics/schedules",
                    "PUT",
                    scheduleData(false),
                  );
                  setCollect(null);
                  await reload();
                })
              }
            >
              停止此内容周期
            </button>
          </div>
        </Modal>
      )}
      {history && (
        <Modal isOpen title="指标变化与来源" onClose={() => setHistory(null)}>
          <p>按采集时间倒序；相同来源与覆盖才能比较。缺失不补零。</p>
          {history.map((s) => (
            <details key={s.id}>
              <summary>
                {new Date(s.observed_at).toLocaleString()} —{" "}
                {Object.entries(s.metrics)
                  .map(([k, v]) => `${labels[k]}:${metricText(v)}`)
                  .join(" / ")}
              </summary>
              {json(s)}
            </details>
          ))}
        </Modal>
      )}
      {report && (
        <Modal
          isOpen
          title={`${kinds[report.kind]} · ${report.status}`}
          onClose={() => setReport(null)}
        >
          <div className="social-analytics-form">
            <p>
              固定画像 {report.input.profile_revision_id}；原内容版本{" "}
              {report.input.contents.map((c) => `v${c.revision}`).join("、")}
              。解释是待验证假设，评论为有限样本。
            </p>
            {report.result ? (
              <>
                <h3>观察事实（程序计算）</h3>
                {report.result.observed_facts.map((f) => (
                  <p key={f.id}>
                    <button
                      onClick={() =>
                        setEvidence({
                          fact: f,
                          source: report.input.sources.find(
                            (s) => s.id === f.snapshot_id,
                          ),
                        })
                      }
                    >
                      {labels[f.metric]}：{f.value} — 查看快照
                    </button>
                  </p>
                ))}
                <h3>可能原因（待验证）</h3>
                <ul>{conclusions(report.result.possible_causes)}</ul>
                <h3>证据不足</h3>
                <ul>
                  {report.result.insufficient_evidence.map((t, i) => (
                    <li key={i}>{t}</li>
                  ))}
                </ul>
                <h3>评论需求与问题</h3>
                {json(report.result.comment_coverage)}
                <ul>{conclusions(report.result.comment_insights)}</ul>
                <h3>可确认的建议</h3>
                {report.suggestions.map((s) => (
                  <div key={s.id}>
                    <p>
                      {s.kind === "experience"
                        ? "补充画像经验（新版本）"
                        : "生成选题计划"}{" "}
                      · {s.decision}
                    </p>
                    <p>原建议：{s.original_text}</p>
                    {refs(s.evidence)}
                    {s.decision === "pending" ? (
                      <>
                        <label>
                          编辑后采用
                          <textarea
                            value={edits[s.id] ?? s.original_text}
                            onChange={(e) =>
                              setEdits({ ...edits, [s.id]: e.target.value })
                            }
                          />
                        </label>
                        {(["accept", "reject"] as const).map((decision) => (
                          <button
                            key={decision}
                            disabled={
                              busy ||
                              (decision === "accept" &&
                                !(edits[s.id] ?? s.original_text).trim())
                            }
                            onClick={() =>
                              void act(async () => {
                                const p = profiles.find(
                                  (p) =>
                                    p.id === report.input.profile_id ||
                                    p.revision_id ===
                                      report.input.profile_revision_id,
                                );
                                await socialApi(
                                  `/analytics/suggestions/${s.id}/decision`,
                                  "POST",
                                  {
                                    decision,
                                    text: edits[s.id] ?? s.original_text,
                                    expected_profile_revision:
                                      p?.current_revision ?? null,
                                  },
                                );
                                setReport(
                                  await socialApi<Review>(
                                    `/analytics/reviews/${report.id}`,
                                  ),
                                );
                                await onChanged?.();
                                setNotice(
                                  decision === "accept"
                                    ? "已生成新版本/计划，原内容与排期授权保留。"
                                    : "已拒绝，画像与计划未改变。",
                                );
                              })
                            }
                          >
                            {decision === "accept" ? "确认接受" : "拒绝"}
                          </button>
                        ))}
                      </>
                    ) : (
                      json(s.result)
                    )}
                  </div>
                ))}
                <details>
                  <summary>统计与覆盖</summary>
                  {json(report.result.statistics)}
                </details>
              </>
            ) : (
              <p>报告尚未生成。{report.error}</p>
            )}
            <details>
              <summary>固定输入、模型与用量记录</summary>
              {json({
                input: report.input,
                outputs: report.outputs,
                usage: report.usage,
              })}
            </details>
          </div>
        </Modal>
      )}
      {evidence !== null && (
        <Modal isOpen title="结论数据及来源" onClose={() => setEvidence(null)}>
          {json(evidence)}
        </Modal>
      )}
    </section>
  );
}
