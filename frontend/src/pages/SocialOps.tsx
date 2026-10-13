import { useEffect, useState, useRef } from "react";
import {
  Sparkles,
  ArrowLeft,
  AlertCircle,
  Check,
  Settings2,
  Plus,
  RefreshCw,
  NotebookPen,
  X,
} from "lucide-react";
import { useAuthStore } from "../stores/useAuthStore";
import "./SocialOps.css";
import PublishCalendar from "../components/social-ops/PublishCalendar";
import ContentHistory from "../components/social-ops/ContentHistory";
import CardStudio, {
  SocialAssetPreview,
} from "../components/social-ops/CardStudio";
import { Link } from "react-router-dom";
import { toApiRequestError } from "../services/api";
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
const buttonClass = "social-primary disabled:opacity-40";
function JsonView({ value }: { value: unknown }) {
  return (
    <pre className="whitespace-pre-wrap break-words rounded-lg bg-surface p-3 text-xs">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export default function SocialOps() {
  const user = useAuthStore((state) => state.user);
  const [profileOpen, setProfileOpen] = useState(false);
  const profileDialog = useRef<HTMLDialogElement>(null);
  const [loaded, setLoaded] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [editorMode, setEditorMode] = useState<"edit" | "preview">("edit");
  const [dirty, setDirty] = useState(false);
  const [mobileConfigOpen, setMobileConfigOpen] = useState(false);
  useEffect(() => {
    if (profileOpen) profileDialog.current?.showModal();
  }, [profileOpen]);
  const detailVersion = useRef(0);
  const [sourceOffset, setSourceOffset] = useState(0);
  const [selectedSource, setSelectedSource] = useState<Source | null>(null);
  const [tab, setTab] = useState<"research" | "plan" | "editor">("research");
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
  const [topicDates, setTopicDates] = useState<Record<string, string>>({});
  const execute = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (e) {
      const failure = toApiRequestError(e);
      setUnavailable(failure.status === 503);
      setError(
        failure.status === 409
          ? "数据版本或任务状态已变化，本次操作未保存。请先保留未保存文字，再查看最新版本或刷新任务后重试。"
          : failure.message,
      );
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
      setLoaded(true);
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
    if (dirty) return;
    setDraft((current) => {
      if (!current) return current;
      const latest = drafts.find((item) => item.id === current.id);
      return latest && latest.current_revision > current.current_revision
        ? latest
        : current;
    });
  }, [drafts, dirty]);
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
      setDirty(false);
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
  const completed =
    job?.items?.filter((i) => i.status === "completed").length ?? 0;
  const itemCount = job?.items?.length ?? 0;
  const activeSteps = stageKeys[tab === "plan" ? "plan" : "draft"];
  const skillsReady = activeSteps.every((key) => skillVersions[key]);
  const selectJob = (id: string) => {
    if (
      dirty &&
      !window.confirm("当前草稿有未保存修改，是否放弃修改并切换任务？")
    )
      return false;
    setJobId(id);
    setDraft(null);
    setDirty(false);
    setHistory(null);
    return true;
  };
  return (
    <main className="social-workspace">
      <header className="social-header">
        <div className="flex items-center gap-3">
          <div className="social-logo">
            <Sparkles size={21} />
          </div>
          <strong>每日AI</strong>
          <span className="social-divider" />
          <h1>社媒运营</h1>
        </div>
        <div className="flex items-center gap-5">
          <Link to="/chat" className="flex items-center gap-2">
            <ArrowLeft size={16} />
            返回聊天
          </Link>
          <span className="hidden sm:inline">
            剩余积分：<strong>{user?.credits ?? 0}</strong>
          </span>
          <span className="social-avatar">
            {user?.nickname?.charAt(0) || "我"}
          </span>
        </div>
      </header>
      {error && (
        <div role="alert" className="social-alert">
          <AlertCircle size={18} />
          <div>
            <strong>
              {unavailable ? "社媒运营尚未启用或服务暂不可用" : "操作未完成"}
            </strong>
            <p>
              {unavailable
                ? "当前无法加载运营数据，请管理员核对功能开关与服务状态。账号状态尚未确认。"
                : error}
            </p>
          </div>
          <button onClick={() => window.location.reload()}>重新加载</button>
        </div>
      )}
      {notice && (
        <div role="status" className="social-notice">
          <Check size={16} />
          {notice}
        </div>
      )}
      <div className="social-layout">
        <aside
          className={`social-panel social-config ${mobileConfigOpen ? "social-config-open" : ""}`}
          aria-label="当前阶段配置"
        >
          <div className="social-panel-title">
            <span>工作配置</span>
            <Settings2 size={17} />
            <button
              className="social-mobile-config"
              aria-expanded={mobileConfigOpen}
              onClick={() => setMobileConfigOpen(!mobileConfigOpen)}
            >
              {mobileConfigOpen ? "收起配置" : "展开配置"}
            </button>
          </div>
          {tab === "research" ? (
            <section className="space-y-5">
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
                    aria-label="账号"
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
                {loaded && !accounts.length && (
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
                    aria-label="账号画像"
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
                <button
                  type="button"
                  className="text-accent text-sm"
                  onClick={() => {
                    setProfileOpen(true);
                    setHistory(null);
                  }}
                >
                  管理账号画像
                </button>
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
                <details className="social-advanced">
                  <summary>高级采集设置</summary>
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
                </details>
                <button disabled={busy || !loaded} className={buttonClass}>
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
          ) : (
            <div className="space-y-5">
              <div>
                <span className="social-caption">当前研究</span>
                <h2>{job?.plan.keywords.join("、") || "请先选择研究任务"}</h2>
                <p className="social-caption">使用任务创建时固定的画像与来源</p>
              </div>
              <button
                onClick={() => {
                  setProfileOpen(true);
                  setHistory(null);
                }}
                className="text-accent"
              >
                管理账号画像
              </button>
              <div className="social-soft">
                <strong>
                  {skillsReady ? "本阶段技能已就绪" : "本阶段技能待配置"}
                </strong>
                <p>每阶段使用固定审核版本。</p>
              </div>
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
                  导入社媒技能草稿（管理员）
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
                      setSkills(
                        await socialApi<SkillOption[]>("/skill-options"),
                      ),
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
            </div>
          )}
          <details className="social-boundary">
            <summary>采集与草稿范围</summary>
            <p>
              当前采集范围：关键词与指定小红书样本，不支持完整主页同步、作品全集或运营指标。缺图草稿需先补素材；完整图文检查后才能确认立即或定时发布。
            </p>
          </details>
        </aside>
        <section className="social-panel social-results" aria-label="运营成果">
          <div className="social-result-heading">
            <div>
              <span className="social-caption">社媒运营工作台</span>
              <h2>
                {job?.plan.keywords.join("、") ||
                  (job ? "指定样本研究" : "从一次有据研究开始")}
              </h2>
            </div>
            <span className="social-badge">
              {job
                ? `采集${statuses[job.status] || job.status} · 第${job.generation}轮`
                : "尚未选择任务"}
            </span>
          </div>
          {loaded && !unavailable && <PublishCalendar accounts={accounts} />}
          <nav className="social-stages" aria-label="运营阶段">
            {(
              [
                ["research", "采集来源"],
                ["plan", "选题计划"],
                ["editor", "文案编辑"],
              ] as const
            ).map(([key, label], i) => (
              <button
                key={key}
                aria-current={tab === key ? "step" : undefined}
                onClick={() => {
                  setTab(key);
                  setHistory(null);
                }}
              >
                <span aria-hidden="true">{i + 1}</span>
                {label}
              </button>
            ))}
          </nav>
          {job && tab === "research" && (
            <div className="social-progress">
              <div>
                <span>本轮采集进度</span>
                <span>
                  {completed}/{itemCount} 项
                </span>
              </div>
              <progress value={completed} max={itemCount || 1} />
            </div>
          )}
          {!loaded && !error && (
            <div role="status" className="social-empty">
              正在加载工作台…
            </div>
          )}
          {tab === "research" && (
            <section className="space-y-5">
              <div className="flex justify-between">
                <h2 className="text-lg">采集来源</h2>
                <button onClick={() => void execute(loadJobs)}>刷新列表</button>
              </div>

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
                        disabled={
                          busy ||
                          (a === "pause" &&
                            (job.status === "cancelled" ||
                              (["completed", "partial", "failed"].includes(
                                job.status,
                              ) &&
                                !job.next_occurrence_at))) ||
                          (a === "resume" &&
                            ![
                              "paused_auth",
                              "paused_platform",
                              "needs_input",
                            ].includes(job.status)) ||
                          (a === "cancel" && job.status === "cancelled")
                        }
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
                  {["completed", "partial", "failed"].includes(job.status) &&
                    !job.plan.interval_hours && (
                      <p className="text-sm text-text-secondary">
                        单次采集已结束，无后续采集计划。
                      </p>
                    )}
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
                    来源 {sources.length ? sourceOffset + 1 : 0}–
                    {sourceOffset + sources.length} /{" "}
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
                    {s.title} ·{" "}
                    {(
                      {
                        full_text: "正文",
                        snippet: "搜索摘要",
                        comment: "评论",
                        comments: "评论",
                      } as Record<string, string>
                    )[s.coverage.content_kind] || s.coverage.content_kind}{" "}
                    · 第{s.generation}轮{" "}
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
          )}
          {(tab === "plan" || tab === "editor") && (
            <section className="space-y-5">
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
                          {t.planned_date || "日期待定"} ·{" "}
                          {(
                            {
                              adopt: "采用",
                              adjust: "调整",
                              defer: "暂缓",
                            } as Record<string, string>
                          )[t.decision] || t.decision}
                        </p>
                        <p>{t.angle}</p>
                        <p className="text-sm">
                          所需素材：{t.required_materials.join("、")} · 未知项：
                          {t.unknowns.join("、")}
                        </p>
                        {t.evidence.map((e, i) => (
                          <blockquote
                            key={i}
                            className="text-sm border-l-2 pl-2"
                          >
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
                            value={topicDates[saved.id] ?? t.planned_date ?? ""}
                            disabled={busy}
                            onChange={(e) =>
                              setTopicDates((dates) => ({
                                ...dates,
                                [saved.id]: e.target.value,
                              }))
                            }
                            onBlur={(e) => {
                              const date = e.currentTarget.value;
                              if (
                                !e.currentTarget.validity.valid ||
                                date === (t.planned_date || "")
                              )
                                return;
                              void execute(async () => {
                                await socialApi(
                                  `/topics/${saved.id}`,
                                  "PATCH",
                                  {
                                    expected_revision: saved.revision,
                                    decision: t.decision,
                                    planned_date: date || null,
                                  },
                                );
                                await loadDetail(jobId);
                                setTopicDates((dates) => {
                                  const next = { ...dates };
                                  delete next[saved.id];
                                  return next;
                                });
                              });
                            }}
                          />
                        </label>
                        <p className="text-xs text-text-secondary">
                          离开日期框后自动保存
                        </p>
                        <label>
                          选题决定
                          <select
                            value={t.decision}
                            disabled={busy}
                            onChange={(e) =>
                              void execute(async () => {
                                await socialApi(
                                  `/topics/${saved.id}`,
                                  "PATCH",
                                  {
                                    expected_revision: saved.revision,
                                    decision: e.target.value,
                                    planned_date: t.planned_date,
                                  },
                                );
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
                          disabled={busy}
                          onClick={() =>
                            void execute(async () => {
                              const adopted = await socialApi<SavedTopic>(
                                `/topics/${saved.id}`,
                                "PATCH",
                                {
                                  expected_revision: saved.revision,
                                  decision: "adopt",
                                  planned_date: t.planned_date,
                                },
                              );
                              await loadDetail(jobId);
                              setSelectedTopic(adopted);
                              setTopic(
                                adopted.data.title + "：" + adopted.data.angle,
                              );
                              setTab("editor");
                            })
                          }
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
                      if (
                        dirty &&
                        !window.confirm(
                          "当前草稿有未保存修改，是否放弃修改并切换草稿？",
                        )
                      )
                        return;
                      setDraft(
                        drafts.find((d) => d.id === e.target.value) ?? null,
                      );
                      setHistory(null);
                      setDirty(false);
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
                      {dirty &&
                        drafts.some(
                          (item) =>
                            item.id === draft.id &&
                            item.current_revision > draft.current_revision,
                        ) && (
                          <p role="status" className="text-sm text-amber-700">
                            服务器已有更新版本，当前未保存修改已保留。保存前请核对版本历史。
                          </p>
                        )}
                      <div className="flex justify-between items-center">
                        <div className="social-segment">
                          <button
                            aria-pressed={editorMode === "edit"}
                            onClick={() => setEditorMode("edit")}
                          >
                            编辑
                          </button>
                          <button
                            aria-pressed={editorMode === "preview"}
                            onClick={() => setEditorMode("preview")}
                          >
                            预览
                          </button>
                        </div>
                        <span className="text-sm text-text-secondary">
                          {dirty
                            ? "有未保存修改"
                            : `已保存 · v${draft.current_revision}`}
                        </span>
                      </div>
                      <div className="social-draft">
                        <div
                          className="space-y-3"
                          hidden={editorMode !== "edit"}
                        >
                          <label>
                            标题
                            <input
                              className={inputClass}
                              value={draft.data.title}
                              onChange={(e) => {
                                setDirty(true);
                                setDraft({
                                  ...draft,
                                  data: {
                                    ...draft.data,
                                    title: e.target.value,
                                  },
                                });
                              }}
                            />
                          </label>
                          <label className="block">
                            正文
                            <textarea
                              rows={15}
                              className={inputClass}
                              value={draft.data.body}
                              onChange={(e) => {
                                setDirty(true);
                                setDraft({
                                  ...draft,
                                  data: { ...draft.data, body: e.target.value },
                                });
                              }}
                            />
                          </label>
                          <label>
                            标签（逗号分隔）
                            <input
                              className={inputClass}
                              value={draft.data.tags.join(",")}
                              onChange={(e) => {
                                setDirty(true);
                                setDraft({
                                  ...draft,
                                  data: {
                                    ...draft.data,
                                    tags: e.target.value
                                      .split(",")
                                      .filter(Boolean),
                                  },
                                });
                              }}
                            />
                          </label>
                        </div>
                        <aside
                          hidden={editorMode !== "preview"}
                          className="mx-auto w-full max-w-sm rounded-3xl border p-5 bg-surface"
                        >
                          {draft.data.assets?.length ? (
                            <div className="flex gap-2 overflow-x-auto">
                              {draft.data.assets.map((a) => (
                                <div className="min-w-full" key={a.id}>
                                  <SocialAssetPreview id={a.id} />
                                </div>
                              ))}
                            </div>
                          ) : (
                            <div className="aspect-[3/4] bg-gray-200 rounded-xl flex items-center justify-center">
                              待补图片素材
                            </div>
                          )}
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
                          disabled={busy || dirty}
                          onClick={() => void produce("polish")}
                        >
                          润色已保存版本
                        </button>
                        <button
                          disabled={busy || dirty}
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
                      {!draft.data.assets?.length && (
                        <div className="social-materials">
                          <strong>待补素材</strong>
                          <p>
                            当前仅有文字草稿，请补充自有或获授权的图片。尚不具备发布条件。
                          </p>
                        </div>
                      )}
                      <CardStudio
                        draft={draft}
                        dirty={dirty}
                        jobId={jobId}
                        runs={runs}
                        accounts={accounts}
                        skills={skills}
                        onRefresh={() => loadDetail(jobId)}
                      />
                      <details>
                        <summary>查看此版本检查详情</summary>
                        <JsonView value={draft.checks} />
                      </details>
                    </>
                  )}
                </>
              )}
              <h3 className="font-semibold">阶段执行与检查结果</h3>
              {runs.map((r) => (
                <details key={r.id} className="border rounded p-3">
                  <summary>
                    {(
                      {
                        cards: "图卡设计与制作",
                        plan: "选题分析",
                        draft: "草稿创作",
                        polish: "文案润色",
                        check: "质量与素材检查",
                      } as Record<string, string>
                    )[r.kind] || r.kind}{" "}
                    · {statuses[r.status] || r.status} · 已完成
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
                <button onClick={() => setSelectedSource(null)}>
                  关闭来源
                </button>
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
          {history && !profileOpen && (
            <section className="bg-surface-card rounded p-5">
              <h2>版本历史</h2>
              <ContentHistory rows={history} />
            </section>
          )}
        </section>
        <aside
          className="social-panel social-history"
          aria-label="研究任务历史"
        >
          <button
            className={
              buttonClass + " w-full flex items-center justify-center gap-2"
            }
            onClick={() => {
              if (!selectJob("")) return;
              setTab("research");
              setKeywords("");
              setUrls("");
            }}
          >
            <Plus size={18} />
            新建研究任务
          </button>
          <div className="social-panel-title">
            <h2>研究任务</h2>
            <button
              aria-label="刷新任务列表"
              onClick={() => void execute(loadJobs)}
            >
              <RefreshCw size={16} />
            </button>
          </div>
          <label className="social-caption">
            {tab === "research" ? "选择研究任务" : "研究任务"}
            <select
              className={inputClass}
              aria-label={tab === "research" ? "选择研究任务" : "研究任务"}
              value={jobId}
              onChange={(e) => selectJob(e.target.value)}
            >
              <option value="">选择研究任务</option>
              {jobs.map((j) => (
                <option key={j.id} value={j.id}>
                  {j.plan.keywords.join("、") || "指定样本"} ·{" "}
                  {statuses[j.status] || j.status}
                </option>
              ))}
            </select>
          </label>
          <div className="social-job-list">
            {jobs.map((j) => (
              <button
                key={j.id}
                aria-pressed={jobId === j.id}
                onClick={() => selectJob(j.id)}
              >
                <div className="social-job-icon">
                  <NotebookPen size={20} />
                </div>
                <div>
                  <strong>
                    {j.plan.keywords.join("、") || "指定样本研究"}
                  </strong>
                  <span>
                    采集{statuses[j.status] || j.status} · 第{j.generation}轮
                  </span>
                </div>
              </button>
            ))}
          </div>
          {loaded && !jobs.length && (
            <div className="social-empty">
              <NotebookPen size={28} />
              <p>还没有研究任务</p>
              <span>配置账号与画像，开始第一次采集。</span>
            </div>
          )}
          <p className="social-history-note">
            任务提交后在后台继续执行，可以离开页面后回来查看。采集完成后，由你启动选题与文案生成。
          </p>
        </aside>
      </div>
      {profileOpen && (
        <dialog
          ref={profileDialog}
          aria-label="账号画像管理"
          className="social-profile-dialog"
          onCancel={() => {
            setProfileOpen(false);
            setHistory(null);
          }}
        >
          <div className="social-panel-title">
            <h2>账号画像</h2>
            <button
              aria-label="关闭画像管理"
              onClick={() => {
                setProfileOpen(false);
                setHistory(null);
              }}
            >
              <X size={20} />
            </button>
          </div>
          {notice && (
            <p role="status" className="social-notice">
              {notice}
            </p>
          )}
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
          {history && (
            <details open>
              <summary>画像版本历史</summary>
              <JsonView value={history} />
            </details>
          )}
        </dialog>
      )}
    </main>
  );
}
