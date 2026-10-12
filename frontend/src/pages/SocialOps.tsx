import { useEffect, useState, useRef } from "react";
import { Link } from "react-router-dom";
import { socialApi, connections, stageKeys } from "../services/socialOps";
import type {
  ProfileData,
  Profile,
  Job,
  Source,
  Draft,
  Run,
  SkillOption,
  Connection,
  Budget,
  SavedTopic,
} from "../services/socialOps";

const emptyProfile: ProfileData = {
  name: "",
  positioning: "",
  audience: "",
  facts: "",
  pillars: [],
  tone: "",
  prohibited: "",
  experience: "",
  visual_template: "",
};
const statuses: Record<string, string> = {
  queued: "待处理",
  running: "处理中",
  waiting_budget: "等待访问窗口",
  paused_auth: "账号异常，已暂停",
  paused_platform: "平台异常，已暂停",
  needs_input: "需要处理",
  completed: "已完成",
  partial: "部分完成",
  failed: "失败",
  cancelled: "已取消",
  paused: "已暂停",
};
const inputClass =
  "w-full rounded-lg border border-border-default p-2 bg-surface-card text-text-primary";
const buttonClass =
  "rounded-lg bg-accent text-text-on-accent px-4 py-2 disabled:opacity-40";
function JsonView({ value }: { value: unknown }) {
  return (
    <pre className="whitespace-pre-wrap break-words rounded-lg bg-surface p-3 text-xs">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export default function SocialOps() {
  const detailVersion = useRef(0);
  const [sourceOffset, setSourceOffset] = useState(0);
  const [selectedSource, setSelectedSource] = useState<Source | null>(null);
  const [tab, setTab] = useState<"research" | "profile" | "plan" | "editor">(
    "research",
  );
  const [profiles, setProfiles] = useState<Profile[]>([]),
    [profileId, setProfileId] = useState("");
  const [profile, setProfile] = useState<ProfileData>(emptyProfile),
    [editingProfile, setEditingProfile] = useState<Profile | null>(null);
  const [accounts, setAccounts] = useState<Connection[]>([]),
    [connectionId, setConnectionId] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]),
    [jobId, setJobId] = useState(""),
    [job, setJob] = useState<Job | null>(null);
  const [topics, setTopics] = useState<SavedTopic[]>([]);
  const [sources, setSources] = useState<Source[]>([]),
    [runs, setRuns] = useState<Run[]>([]),
    [drafts, setDrafts] = useState<Draft[]>([]);
  const [skills, setSkills] = useState<SkillOption[]>([]),
    [budget, setBudget] = useState<Budget | null>(null);
  const [keywords, setKeywords] = useState(""),
    [urls, setUrls] = useState(""),
    [maxItems, setMaxItems] = useState(5),
    [pages, setPages] = useState(0),
    [hours, setHours] = useState(0);
  const [selectedTopic, setSelectedTopic] = useState<SavedTopic | null>(null);
  const [credits, setCredits] = useState(20),
    [topic, setTopic] = useState(""),
    [draft, setDraft] = useState<Draft | null>(null);
  const [history, setHistory] = useState<unknown[] | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState("");
  const [skillVersions, setSkillVersions] = useState<Record<string, string>>(
    {},
  );
  const execute = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败，请检查权限或配置");
    } finally {
      setBusy(false);
    }
  };
  const loadJobs = async () =>
    setJobs(await socialApi<Job[]>("/research-jobs"));
  const loadDetail = async (id: string) => {
    const version = ++detailVersion.current;
    const [j, s, r, d, t] = await Promise.all([
      socialApi<Job>(`/research-jobs/${id}`),
      socialApi<Source[]>(
        `/research-jobs/${id}/sources${sourceOffset ? `?offset=${sourceOffset}&limit=100` : ""}`,
      ),
      socialApi<Run[]>(`/research-jobs/${id}/production-runs`),
      socialApi<Draft[]>(`/research-jobs/${id}/contents`),
      socialApi<SavedTopic[]>(`/topics?job_id=${id}`),
    ]);
    if (version !== detailVersion.current) return;
    setJob(j);
    setSources(s);
    setRuns(r);
    setDrafts(d);
    setTopics(t);
  };
  useEffect(() => {
    let active = true;
    void execute(async () => {
      const [p, a, j, s] = await Promise.all([
        socialApi<Profile[]>("/profiles"),
        connections(),
        socialApi<Job[]>("/research-jobs"),
        socialApi<SkillOption[]>("/skill-options"),
      ]);
      if (!active) return;
      setProfiles(p);
      setAccounts(a.filter((x) => x.platform === "xiaohongshu"));
      setJobs(j);
      setSkills(s);
      setSkillVersions(
        Object.fromEntries(s.map((x) => [x.skill_id, x.revision])),
      );
    });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    if (!jobId) return;
    setJob(null);
    setSources([]);
    setRuns([]);
    setDrafts([]);
    setTopics([]);

    let active = true;
    const refresh = () =>
      loadDetail(jobId).catch((e) => {
        if (active) setError(String(e));
      });
    void refresh();
    const timer = setInterval(() => {
      if (active) void refresh();
    }, 5000);
    return () => {
      active = false;
      detailVersion.current++;
      clearInterval(timer);
    };
  }, [jobId, sourceOffset]);
  useEffect(() => {
    if (!connectionId) {
      setBudget(null);
      return;
    }
    void socialApi<Budget>(`/connections/${connectionId}/budget`)
      .then(setBudget)
      .catch((e) => setError(String(e)));
  }, [connectionId]);
  useEffect(() => {
    setSourceOffset(0);
    setSelectedTopic(null);
    setTopic("");
    setSelectedSource(null);
  }, [jobId]);
  const produce = (kind: string) =>
    execute(async () => {
      if (!jobId) throw new Error("请先选择研究任务");
      const stages = stageKeys[kind].map((skill_id) => ({
        skill_id,
        revision: skillVersions[skill_id],
      }));
      if (stages.some((x) => !x.revision))
        throw new Error(
          "缺少已审核的技能版本，请管理员导入并完成审核、发布、启用",
        );
      await socialApi("/production-runs", "POST", {
        kind,
        job_id: jobId,
        topic,
        max_credits: credits,
        ...(kind === "draft" && selectedTopic
          ? {
              topic_id: selectedTopic.id,
              expected_topic_revision: selectedTopic.revision,
            }
          : {}),
        stages,
        ...(["polish", "check"].includes(kind)
          ? {
              content_id: draft?.id,
              expected_revision: draft?.current_revision,
            }
          : {}),
      });
      setNotice("已创建文字阶段任务");
      await loadDetail(jobId);
    });
  const saveProfile = () =>
    execute(async () => {
      await socialApi(
        editingProfile ? `/profiles/${editingProfile.id}` : "/profiles",
        editingProfile ? "PATCH" : "POST",
        {
          ...profile,
          ...(editingProfile
            ? { expected_revision: editingProfile.current_revision }
            : {}),
        },
      );
      setProfiles(await socialApi<Profile[]>("/profiles"));
      setEditingProfile(null);
      setProfile(emptyProfile);
      setNotice("画像版本已保存");
    });
  const createJob = () =>
    execute(async () => {
      const j = await socialApi<Job>("/research-jobs", "POST", {
        client_request_id: crypto.randomUUID(),
        connection_id: connectionId,
        profile_id: profileId,
        keywords: keywords
          .split("\n")
          .map((s) => s.trim())
          .filter(Boolean),
        sample_urls: urls
          .split("\n")
          .map((s) => s.trim())
          .filter(Boolean),
        max_items: maxItems,
        comment_pages: pages,
        interval_hours: hours || null,
      });
      setJobId(j.id);
      await loadJobs();
    });
  const saveDraft = () =>
    execute(async () => {
      if (!draft) return;
      await socialApi(`/contents/${draft.id}/revisions`, "POST", {
        expected_revision: draft.current_revision,
        title: draft.data.title,
        body: draft.data.body,
        tags: draft.data.tags,
        evidence_ids:
          draft.data.evidence_ids ??
          draft.data.evidence?.map((e) => e.snapshot_id) ??
          [],
        assumptions: draft.data.assumptions ?? [],
      });
      const ds = await socialApi<Draft[]>(`/research-jobs/${jobId}/contents`);
      setDrafts(ds);
      setDraft(ds.find((x) => x.id === draft.id) ?? null);
      setNotice("已保存新草稿版本；待补图片素材");
    });
  const profileField = (
    key: keyof ProfileData,
    label: string,
    required = false,
  ) => (
    <label className="block space-y-1" key={key}>
      <span>{label}</span>
      <textarea
        required={required}
        className={inputClass}
        value={
          Array.isArray(profile[key])
            ? (profile[key] as string[]).join("\n")
            : profile[key]
        }
        onChange={(e) =>
          setProfile({
            ...profile,
            [key]:
              key === "pillars"
                ? e.target.value.split("\n").filter(Boolean)
                : e.target.value,
          })
        }
      />
    </label>
  );
  return (
    <main className="min-h-screen bg-surface text-text-primary p-4 md:p-8">
      <div className="mx-auto max-w-6xl space-y-5">
        <header className="flex justify-between items-center">
          <div>
            <h1 className="text-2xl font-semibold">社媒运营</h1>
            <p className="text-sm text-text-secondary">
              持续研究 · 有据选题 · 文字草稿
            </p>
          </div>
          <Link to="/" className="text-accent">
            返回工作区
          </Link>
        </header>
        <p className="rounded-lg bg-amber-50 p-3 text-sm">
          当前采集范围：关键词与指定小红书样本，不支持完整主页同步、作品全集或运营指标。文字草稿待补图片素材，本页面不发布内容。
        </p>
        {error && (
          <p role="alert" className="bg-red-50 text-red-700 p-3 rounded-lg">
            {error}
          </p>
        )}
        {notice && (
          <p role="status" className="bg-green-50 p-3">
            {notice}
          </p>
        )}
        <nav className="flex gap-2" aria-label="社媒运营页面">
          {(
            [
              ["research", "研究任务"],
              ["profile", "账号画像"],
              ["plan", "选题计划"],
              ["editor", "文案编辑"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              onClick={() => {
                setTab(key);
                setHistory(null);
              }}
              className={`rounded-lg px-4 py-2 ${tab === key ? "bg-accent text-text-on-accent" : "bg-surface-card border"}`}
            >
              {label}
            </button>
          ))}
        </nav>
        {tab === "profile" && (
          <section className="bg-surface-card rounded-xl p-5 space-y-3">
            <h2 className="text-lg">画像管理</h2>
            <div className="flex flex-wrap gap-2">
              {profiles.map((p) => (
                <button
                  className="border rounded p-2"
                  key={p.id}
                  onClick={() => {
                    setEditingProfile(p);
                    setProfile(p.data);
                  }}
                >
                  {p.data.name} · v{p.current_revision}
                </button>
              ))}
              <button
                onClick={() => {
                  setEditingProfile(null);
                  setProfile(emptyProfile);
                }}
              >
                新建画像
              </button>
            </div>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void saveProfile();
              }}
              className="grid md:grid-cols-2 gap-3"
            >
              {profileField("name", "名称", true)}
              {profileField("positioning", "定位与专业优势", true)}
              {profileField("audience", "目标读者与问题", true)}
              {profileField("facts", "可核实品牌事实与产品资料")}
              {profileField("pillars", "内容支柱（每行一项）")}
              {profileField("tone", "语言语气")}
              {profileField("prohibited", "禁止表达")}
              {profileField("experience", "真实可引用经验")}
              {profileField("visual_template", "视觉模板需求（后续制作）")}
              <div className="flex gap-3 items-end">
                <button disabled={busy} className={buttonClass}>
                  保存画像新版本
                </button>
                {editingProfile && (
                  <button
                    type="button"
                    onClick={() =>
                      void execute(async () =>
                        setHistory(
                          await socialApi<unknown[]>(
                            `/profiles/${editingProfile.id}/revisions`,
                          ),
                        ),
                      )
                    }
                  >
                    版本历史
                  </button>
                )}
              </div>
            </form>
          </section>
        )}
        {tab === "research" && (
          <div className="grid md:grid-cols-3 gap-5">
            <section className="bg-surface-card rounded-xl p-5 space-y-3">
              <h2 className="text-lg">创建研究任务</h2>
              <form
                className="space-y-3"
                onSubmit={(e) => {
                  e.preventDefault();
                  void createJob();
                }}
              >
                <label className="block">
                  账号
                  <select
                    required
                    className={inputClass}
                    value={connectionId}
                    onChange={(e) => setConnectionId(e.target.value)}
                  >
                    <option value="">选择可用账号</option>
                    {accounts.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.display_name} · {a.login_state}
                      </option>
                    ))}
                  </select>
                </label>
                {!accounts.length && (
                  <p className="text-sm">
                    暂无可用账号，请联系管理员在
                    <Link to="/admin?tab=accounts" className="text-accent">
                      账号连接
                    </Link>
                    配置。
                  </p>
                )}
                <label className="block">
                  账号画像
                  <select
                    required
                    className={inputClass}
                    value={profileId}
                    onChange={(e) => setProfileId(e.target.value)}
                  >
                    <option value="">选择画像</option>
                    {profiles.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.data.name} · v{p.current_revision}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block">
                  关键词（每行一个）
                  <textarea
                    className={inputClass}
                    value={keywords}
                    onChange={(e) => setKeywords(e.target.value)}
                  />
                </label>
                <label className="block">
                  指定样本链接（每行一个；需搜索返回的完整链接）
                  <textarea
                    className={inputClass}
                    value={urls}
                    onChange={(e) => setUrls(e.target.value)}
                  />
                </label>
                <label className="block">
                  每轮正文上限
                  <input
                    className={inputClass}
                    type="number"
                    min={1}
                    max={30}
                    value={maxItems}
                    onChange={(e) => setMaxItems(Number(e.target.value))}
                  />
                </label>
                <label className="block">
                  评论页数（0–3）
                  <input
                    className={inputClass}
                    type="number"
                    min={0}
                    max={3}
                    value={pages}
                    onChange={(e) => setPages(Number(e.target.value))}
                  />
                </label>
                <label className="block">
                  重复间隔小时（0为单次）
                  <input
                    className={inputClass}
                    type="number"
                    min={0}
                    max={720}
                    value={hours}
                    onChange={(e) => setHours(Number(e.target.value))}
                  />
                </label>
                <button disabled={busy} className={buttonClass}>
                  创建并开始采集
                </button>
              </form>
              {budget && (
                <details>
                  <summary>
                    账号访问预算：已占用 {budget.consumed}/{budget.daily_limit}
                  </summary>
                  <p className="text-xs">
                    HTTP与浏览器内部成本采用保守估计，额度与间隔不代表平台安全保证。配置及异常恢复仅管理员可操作。
                  </p>
                  <label>
                    每日额度
                    <input
                      className={inputClass}
                      type="number"
                      min={2}
                      value={budget.daily_limit}
                      onChange={(e) =>
                        setBudget({
                          ...budget,
                          daily_limit: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                  <label>
                    跨调用间隔秒
                    <input
                      className={inputClass}
                      type="number"
                      min={1}
                      value={budget.min_interval_seconds}
                      onChange={(e) =>
                        setBudget({
                          ...budget,
                          min_interval_seconds: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                  <label>
                    允许开始小时
                    <input
                      className={inputClass}
                      type="number"
                      min={0}
                      max={23}
                      value={budget.allowed_start_hour}
                      onChange={(e) =>
                        setBudget({
                          ...budget,
                          allowed_start_hour: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                  <label>
                    允许结束小时
                    <input
                      className={inputClass}
                      type="number"
                      min={1}
                      max={24}
                      value={budget.allowed_end_hour}
                      onChange={(e) =>
                        setBudget({
                          ...budget,
                          allowed_end_hour: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                  <button
                    disabled={busy}
                    onClick={() =>
                      void execute(async () =>
                        setBudget(
                          await socialApi<Budget>(
                            `/connections/${connectionId}/budget`,
                            "PUT",
                            {
                              daily_limit: budget.daily_limit,
                              min_interval_seconds: budget.min_interval_seconds,
                              timezone: budget.timezone,
                              allowed_start_hour: budget.allowed_start_hour,
                              allowed_end_hour: budget.allowed_end_hour,
                              max_comment_pages: budget.max_comment_pages,
                            },
                          ),
                        ),
                      )
                    }
                  >
                    保存预算
                  </button>
                  {budget.breaker && (
                    <>
                      <p>{budget.breaker}：账号访问已暂停</p>
                      <button
                        disabled={busy}
                        onClick={() =>
                          void execute(async () => {
                            await socialApi(
                              `/connections/${connectionId}/recover`,
                              "POST",
                            );
                            setBudget(
                              await socialApi<Budget>(
                                `/connections/${connectionId}/budget`,
                              ),
                            );
                          })
                        }
                      >
                        已处理异常，恢复账号访问
                      </button>
                    </>
                  )}
                </details>
              )}
            </section>
            <section className="md:col-span-2 bg-surface-card rounded-xl p-5 space-y-4">
              <div className="flex justify-between">
                <h2 className="text-lg">研究任务与来源</h2>
                <button onClick={() => void execute(loadJobs)}>刷新列表</button>
              </div>
              <select
                aria-label="选择研究任务"
                className={inputClass}
                value={jobId}
                onChange={(e) => setJobId(e.target.value)}
              >
                <option value="">选择研究任务</option>
                {jobs.map((j) => (
                  <option key={j.id} value={j.id}>
                    {j.plan.keywords.join("、") || "指定样本"} ·{" "}
                    {statuses[j.status] || j.status}
                  </option>
                ))}
              </select>
              {job && (
                <>
                  <p>
                    {statuses[job.status] || job.status} · 第 {job.generation}{" "}
                    轮 ·{" "}
                    {
                      job.items?.filter(
                        (i) =>
                          i.status === "completed" &&
                          i.generation === job.generation,
                      ).length
                    }
                    /
                    {
                      job.items?.filter((i) => i.generation === job.generation)
                        .length
                    }{" "}
                    项完成
                  </p>
                  <p className="text-sm">
                    下次可执行：
                    {new Date(job.next_eligible_at).toLocaleString()}{" "}
                    {job.next_occurrence_at &&
                      ` · 下轮：${new Date(job.next_occurrence_at).toLocaleString()}`}
                  </p>
                  {job.last_error && (
                    <p role="alert">
                      {job.last_error}，请处理账号或任务配置后恢复。
                    </p>
                  )}
                  <div className="flex gap-3">
                    {(["pause", "resume", "cancel"] as const).map((a) => (
                      <button
                        key={a}
                        disabled={busy}
                        className="border rounded px-3 py-1"
                        onClick={() =>
                          void execute(async () => {
                            await socialApi(
                              `/research-jobs/${jobId}/${a}`,
                              "POST",
                            );
                            await loadDetail(jobId);
                            await loadJobs();
                          })
                        }
                      >
                        {a === "pause"
                          ? "暂停"
                          : a === "resume"
                            ? "继续剩余队列"
                            : "取消"}
                      </button>
                    ))}
                  </div>
                  <details>
                    <summary>逐项进度</summary>
                    {job.items?.map((i) => (
                      <p className="text-sm" key={i.id}>
                        第{i.generation}轮 · {i.action} / {i.page} ·{" "}
                        {statuses[i.status] || i.status} · 尝试{i.attempts}次{" "}
                        {i.error} {i.result_meta?.warnings?.join("；")}
                        {i.status === "failed" &&
                          i.generation === job.generation && (
                            <button
                              disabled={busy}
                              className="ml-2 text-accent"
                              onClick={() =>
                                void execute(async () => {
                                  await socialApi(
                                    `/research-items/${i.id}/retry`,
                                    "POST",
                                  );
                                  await loadDetail(jobId);
                                })
                              }
                            >
                              重试此项
                            </button>
                          )}
                      </p>
                    ))}
                  </details>
                </>
              )}
              {!sources.length && (
                <p className="text-text-tertiary">
                  暂无来源。搜索成功但无匹配时会明确结束；未取得正文时不能生成有据草稿。
                </p>
              )}
              {job && (
                <div className="flex gap-3 items-center">
                  <button
                    disabled={sourceOffset === 0 || busy}
                    onClick={() =>
                      setSourceOffset(Math.max(0, sourceOffset - 100))
                    }
                  >
                    上一页
                  </button>
                  <span>
                    来源 {sourceOffset + 1}–{sourceOffset + sources.length} /{" "}
                    {job.source_count ?? sources.length}
                  </span>
                  <button
                    disabled={
                      busy ||
                      sourceOffset + sources.length >=
                        (job.source_count ?? sources.length)
                    }
                    onClick={() => setSourceOffset(sourceOffset + 100)}
                  >
                    下一页
                  </button>
                </div>
              )}
              {sources.map((s) => (
                <details className="border rounded-lg p-3" key={s.id}>
                  <summary>
                    {s.title} · {s.coverage.content_kind} · 第{s.generation}轮{" "}
                    {s.coverage.truncated ? "（截断）" : ""}
                  </summary>
                  <a
                    href={s.source_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-accent"
                  >
                    原始来源（令牌已脱敏）
                  </a>
                  <p className="text-xs">
                    {new Date(s.fetched_at).toLocaleString()} ·{" "}
                    {s.coverage.scope}
                  </p>
                  <p className="whitespace-pre-wrap my-2 text-sm">{s.body}</p>
                  <p className="text-xs text-amber-700">
                    缺失：{s.coverage.missing.join("、")}；
                    {s.coverage.warnings.join("；")}
                  </p>
                </details>
              ))}
            </section>
          </div>
        )}
        {(tab === "plan" || tab === "editor") && (
          <section className="bg-surface-card rounded-xl p-5 space-y-4">
            <label>
              研究任务
              <select
                className={inputClass}
                value={jobId}
                onChange={(e) => {
                  setJobId(e.target.value);
                  setDraft(null);
                }}
              >
                <option value="">请选择</option>
                {jobs.map((j) => (
                  <option key={j.id} value={j.id}>
                    {j.plan.keywords.join("、") || "指定样本"} ·{" "}
                    {j.id.slice(0, 8)}
                  </option>
                ))}
              </select>
            </label>
            <details>
              <summary>文字阶段技能版本与积分上限</summary>
              <p className="text-sm">
                每阶段独立激活一个已审核版本，无工具权限。缺少技能时请管理员导入，再前往技能管理审核、发布并启用。
              </p>
              <button
                disabled={busy}
                onClick={() =>
                  void execute(async () => {
                    await socialApi("/skill-drafts", "POST");
                    setNotice(
                      "已导入普通草稿，请在技能管理完成审核、发布和启用",
                    );
                  })
                }
              >
                导入八个技能草稿（管理员）
              </button>
              <Link to="/admin?tab=skills" className="text-accent ml-3">
                技能管理
              </Link>
              {Object.values(stageKeys)
                .flat()
                .map((key) => (
                  <label className="block" key={key}>
                    {key}
                    <select
                      className={inputClass}
                      value={skillVersions[key] || ""}
                      onChange={(e) =>
                        setSkillVersions({
                          ...skillVersions,
                          [key]: e.target.value,
                        })
                      }
                    >
                      <option value="">缺少已审核版本</option>
                      {skills
                        .filter((s) => s.skill_id === key)
                        .map((s) => (
                          <option key={s.revision} value={s.revision}>
                            {s.name} · {s.revision}
                          </option>
                        ))}
                    </select>
                  </label>
                ))}
              <button
                onClick={() =>
                  void execute(async () =>
                    setSkills(await socialApi<SkillOption[]>("/skill-options")),
                  )
                }
              >
                刷新可用技能
              </button>
              <label className="block">
                本次总积分上限
                <input
                  className={inputClass}
                  type="number"
                  min={4}
                  max={1000}
                  value={credits}
                  onChange={(e) => setCredits(Number(e.target.value))}
                />
              </label>
            </details>
            {tab === "plan" && (
              <>
                <button
                  disabled={busy || !jobId}
                  className={buttonClass}
                  onClick={() => void produce("plan")}
                >
                  分析样本并生成选题计划
                </button>
                {!topics.length && <p>暂无已完成的选题计划。</p>}
                {topics.map((saved, index) => {
                  const t = saved.data;
                  return (
                    <article
                      className="border rounded-lg p-4 space-y-2"
                      key={index}
                    >
                      <h3 className="font-semibold">{t.title}</h3>
                      <p>
                        读者：{t.audience} · {t.format} ·{" "}
                        {t.planned_date || "日期待定"} · {t.decision}
                      </p>
                      <p>{t.angle}</p>
                      <p className="text-sm">
                        所需素材：{t.required_materials.join("、")} · 未知项：
                        {t.unknowns.join("、")}
                      </p>
                      {t.evidence.map((e, i) => (
                        <blockquote key={i} className="text-sm border-l-2 pl-2">
                          <button
                            className="text-accent"
                            onClick={() =>
                              void execute(async () =>
                                setSelectedSource(
                                  await socialApi<Source>(
                                    `/source-snapshots/${e.snapshot_id}`,
                                  ),
                                ),
                              )
                            }
                          >
                            来源证据
                          </button>
                          ：{e.quote}
                        </blockquote>
                      ))}
                      <label className="block">
                        计划日期（仅编辑排期建议）
                        <input
                          type="date"
                          value={t.planned_date || ""}
                          onChange={(e) =>
                            void execute(async () => {
                              await socialApi(`/topics/${saved.id}`, "PATCH", {
                                expected_revision: saved.revision,
                                decision: t.decision,
                                planned_date: e.target.value || null,
                              });
                              await loadDetail(jobId);
                            })
                          }
                        />
                      </label>
                      <label>
                        选题决定
                        <select
                          value={t.decision}
                          onChange={(e) =>
                            void execute(async () => {
                              await socialApi(`/topics/${saved.id}`, "PATCH", {
                                expected_revision: saved.revision,
                                decision: e.target.value,
                                planned_date: t.planned_date,
                              });
                              await loadDetail(jobId);
                            })
                          }
                        >
                          <option value="adopt">采用</option>
                          <option value="adjust">调整</option>
                          <option value="defer">暂缓</option>
                        </select>
                      </label>
                      <button
                        className={buttonClass}
                        onClick={() => {
                          setSelectedTopic(saved);
                          setTopic(t.title + "：" + t.angle);
                          setTab("editor");
                        }}
                      >
                        采用并准备草稿
                      </button>
                    </article>
                  );
                })}
              </>
            )}
            {tab === "editor" && (
              <>
                <label>
                  选题简报
                  <textarea
                    className={inputClass}
                    value={topic}
                    onChange={(e) => setTopic(e.target.value)}
                  />
                </label>
                <button
                  className={buttonClass}
                  disabled={busy || !topic || !jobId}
                  onClick={() => void produce("draft")}
                >
                  生成文字草稿
                </button>
                <select
                  aria-label="选择草稿"
                  className={inputClass}
                  value={draft?.id || ""}
                  onChange={(e) => {
                    setDraft(
                      drafts.find((d) => d.id === e.target.value) ?? null,
                    );
                    setHistory(null);
                  }}
                >
                  <option value="">选择草稿</option>
                  {drafts.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.data.title} · v{d.current_revision}
                    </option>
                  ))}
                </select>
                {draft && (
                  <>
                    <div className="grid md:grid-cols-2 gap-5">
                      <div className="space-y-3">
                        <label>
                          标题
                          <input
                            className={inputClass}
                            value={draft.data.title}
                            onChange={(e) =>
                              setDraft({
                                ...draft,
                                data: { ...draft.data, title: e.target.value },
                              })
                            }
                          />
                        </label>
                        <label className="block">
                          正文
                          <textarea
                            rows={15}
                            className={inputClass}
                            value={draft.data.body}
                            onChange={(e) =>
                              setDraft({
                                ...draft,
                                data: { ...draft.data, body: e.target.value },
                              })
                            }
                          />
                        </label>
                        <label>
                          标签（逗号分隔）
                          <input
                            className={inputClass}
                            value={draft.data.tags.join(",")}
                            onChange={(e) =>
                              setDraft({
                                ...draft,
                                data: {
                                  ...draft.data,
                                  tags: e.target.value
                                    .split(",")
                                    .filter(Boolean),
                                },
                              })
                            }
                          />
                        </label>
                      </div>
                      <aside className="mx-auto w-full max-w-sm rounded-3xl border p-5 bg-surface">
                        <div className="aspect-[3/4] bg-gray-200 rounded-xl flex items-center justify-center">
                          待补图片素材
                        </div>
                        <h3 className="font-semibold mt-3">
                          {draft.data.title}
                        </h3>
                        <p className="whitespace-pre-wrap text-sm mt-2">
                          {draft.data.body}
                        </p>
                        <p className="text-accent text-sm mt-3">
                          {draft.data.tags.map((t) => "#" + t).join(" ")}
                        </p>
                      </aside>
                    </div>
                    <p className="text-amber-700 text-sm">
                      编辑后请保存新版本，再运行润色或检查。检查结果绑定保存的版本；缺图草稿不可视为已可发布。
                    </p>
                    <div className="flex flex-wrap gap-3">
                      <button
                        disabled={busy}
                        className={buttonClass}
                        onClick={() => void saveDraft()}
                      >
                        保存新版本
                      </button>
                      <button
                        disabled={busy}
                        onClick={() => void produce("polish")}
                      >
                        润色已保存版本
                      </button>
                      <button
                        disabled={busy}
                        onClick={() => void produce("check")}
                      >
                        检查已保存版本
                      </button>
                      <button
                        onClick={() =>
                          void execute(async () =>
                            setHistory(
                              await socialApi<unknown[]>(
                                `/contents/${draft.id}/revisions`,
                              ),
                            ),
                          )
                        }
                      >
                        版本历史
                      </button>
                    </div>
                    <JsonView value={draft.checks} />
                  </>
                )}
              </>
            )}
            <h3 className="font-semibold">阶段执行与检查结果</h3>
            {runs.map((r) => (
              <details key={r.id} className="border rounded p-3">
                <summary>
                  {r.kind} · {statuses[r.status] || r.status} · 已完成
                  {r.stage_index}阶段 {r.error}
                </summary>
                {r.status === "needs_input" && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      void execute(async () => {
                        await socialApi(
                          `/production-runs/${r.id}/resume`,
                          "POST",
                        );
                        await loadDetail(jobId);
                      })
                    }
                  >
                    继续剩余阶段（受原费用上限约束）
                  </button>
                )}
                <JsonView value={r.result ?? r.outputs} />
              </details>
            ))}
          </section>
        )}
        {selectedSource && (
          <section className="rounded-xl bg-surface-card border border-border-default p-5 space-y-3">
            <div className="flex justify-between">
              <h2>{selectedSource.title}</h2>
              <button onClick={() => setSelectedSource(null)}>关闭来源</button>
            </div>
            <a
              href={selectedSource.source_url}
              target="_blank"
              rel="noreferrer"
              className="text-accent"
            >
              查看原始来源
            </a>
            <p className="whitespace-pre-wrap">{selectedSource.body}</p>
            <JsonView value={selectedSource.coverage} />
          </section>
        )}
        {history && (
          <section className="bg-surface-card rounded p-5">
            <h2>版本历史</h2>
            <JsonView value={history} />
          </section>
        )}
      </div>
    </main>
  );
}
