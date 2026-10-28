import {KnowledgeMediaPlayer} from './KnowledgeMediaPlayer';
import { useEffect, useRef, useState } from "react";
import { Dialog } from "../primitives/Dialog";
import { knowledgeFilesApi } from "../../services/knowledge";
import type {
  CanvasData,
  KnowledgeNode,
  KnowledgeFile,
} from "../../services/knowledge";
import { resolveCanvasLink, safeWebUrl } from "./canvasLinks";
export function CanvasMediaCard({
  node,
  onView,
  onEdit,
  writable,
}: {
  node: KnowledgeNode;
  onView: (source: "video" | "link") => void;
  onEdit: (mode: "video" | "link") => void;
  writable: boolean;
}) {
  const { video, link } = node.canvas_data ?? {};
  const resolved = link ? resolveCanvasLink(link.url, link.kind) : null;
  return (
    <div
      className="kc-media-cards"
      onPointerDown={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
    >
      {video && (
        <div className="kc-media-card">
          <button
            className="kc-media-play"
            onClick={() => onView("video")}
            aria-label={"播放节点视频 " + node.title}
          >
            ▶ {video.title || "上传的视频"}
            <small>固定版本 v{video.revision} · 点击播放</small>
          </button>
          {writable && (
            <button
              aria-label={"编辑视频 " + node.title}
              onClick={() => onEdit("video")}
            >
              编辑
            </button>
          )}
        </div>
      )}
      {link && resolved && (
        <div className="kc-media-card">
          {resolved.mode === "web" ? (
            <a
              href={resolved.url}
              target="_blank"
              rel="noopener noreferrer"
              onPointerDown={(e) => e.stopPropagation()}
            >
              {link.title || new URL(resolved.url).hostname}
              <small>
                {link.kind === "video" ? "前往原平台观看" : "打开原网页"} ↗
              </small>
            </a>
          ) : (
            <button
              className="kc-media-play"
              onClick={() => onView("link")}
              aria-label={"预览视频链接 " + node.title}
            >
              ▶ {link.title || resolved.provider || "视频链接"}
              <small>点击站内预览 · {new URL(resolved.url).hostname}</small>
            </button>
          )}
          {writable && (
            <button
              aria-label={"编辑网址 " + node.title}
              onClick={() => onEdit("link")}
            >
              编辑
            </button>
          )}
        </div>
      )}
    </div>
  );
}
export function CanvasMediaPreview({
  node,
  source,
  onClose,
}: {
  node: KnowledgeNode;
  source: "video" | "link";
  onClose: () => void;
}) {
  const video = source === "video" ? node.canvas_data?.video : null,
    link = source === "link" ? node.canvas_data?.link : null;
  const resolved = link ? resolveCanvasLink(link.url, link.kind) : null;
  const [error, setError] = useState("");
  const src = resolved?.mode === "direct" ? resolved.url : "";
  return (
    <Dialog
      motionPreset="canvas"
      open
      title={video?.title || link?.title || "视频预览"}
      size="xl"
      className="kc-modal kc-video-modal"
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      onCloseAutoFocus={(e) => {
        e.preventDefault();
        document
          .querySelector<HTMLElement>('[aria-label="知识画布"]')
          ?.focus({ preventScroll: true });
      }}
    >
      <div className="kc-video-content">
        {video && <KnowledgeMediaPlayer objectId={node.id} version={{binding_id:video.binding_id,revision:video.revision,name:video.title||'上传的视频.mp4'}} expanded/>}
        {src && (
          <video
            aria-label="节点视频播放器"
            src={src}
            controls
            playsInline
            preload="metadata"
            onError={() =>
              setError(
                video
                  ? "视频无法播放，请使用 H.264/AAC 编码的 MP4，或下载原文件观看。"
                  : "视频直链无法播放，可前往原网页观看。",
              )
            }
          />
        )}{" "}
        {resolved?.mode === "embed" && (
          <>
            <iframe
              title={"视频预览 · " + resolved.provider}
              src={resolved.embed}
              allow="fullscreen; picture-in-picture; encrypted-media"
              allowFullScreen
              sandbox="allow-scripts allow-same-origin allow-presentation"
              referrerPolicy="strict-origin-when-cross-origin"
            />
            <p>如平台要求登录、限制站外播放或未显示视频，请前往原平台观看。</p>
          </>
        )}
        {error && <p role="alert">{error}</p>}
        {resolved && (
          <a href={resolved.url} target="_blank" rel="noopener noreferrer">
            前往原平台观看 ↗
          </a>
        )}
        <button onClick={onClose}>关闭视频</button>
      </div>
    </Dialog>
  );
}
export type MediaDraft={data:CanvasData;uploadUnconfirmed?:boolean};
export function CanvasMediaEditor({
  restored,onDraft,
  node,
  mode,
  pending,
  unknown,
  saveError,
  onCheck,
  onUploaded,
  onSave,
  onClose,
}: {
  restored?:MediaDraft;onDraft?:(draft:MediaDraft)=>void;
  node: KnowledgeNode;
  mode: "video" | "link";
  pending: boolean;
  unknown: boolean;
  saveError: string;
  onCheck: () => void;
  onUploaded: () => Promise<unknown>;
  onSave: (data: CanvasData) => Promise<boolean>;
  onClose: () => void;
}) {
  const initial = node.canvas_data ?? {},
    [data, setData] = useState<CanvasData>(restored?.data??initial),
    [files, setFiles] = useState<KnowledgeFile[]>([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [uncertain, setUncertain] = useState(restored?.uploadUnconfirmed??false);
  useEffect(()=>onDraft?.({data,uploadUnconfirmed:uncertain||busy}),[data,uncertain,busy,onDraft]);
  const attempt = useRef<{ id: string; file: File } | null>(null);
  const locked = pending || unknown || busy || uncertain;
  const link = data.link ?? { url: "", title: "", kind: "auto" as const };
  useEffect(() => {
    if (mode !== "video") return;
    const c = new AbortController();
    void knowledgeFilesApi
      .list(node.id, c.signal)
      .then((rows) => {
        if (!c.signal.aborted)
          setFiles(rows.filter((f) => f.version.mime_type === "video/mp4"));
      })
      .catch(() => {
        if (!c.signal.aborted) setError("读取视频列表失败，请关闭后重试。");
      });
    return () => c.abort();
  }, [node.id, mode]);
  const upload = async (file?: File) => {
    if (file) {
      if (
        !/\.mp4$/i.test(file.name) ||
        file.size > 20 * 1024 * 1024 ||
        !file.size
      ) {
        setError("请选择不超过 20MB 的 MP4 视频，推荐 H.264/AAC 编码。");
        return;
      }
      attempt.current = { file, id: crypto.randomUUID() };
    }
    const request = attempt.current;
    if (!request) {
      if(uncertain){setBusy(true);try{await onUploaded();const rows=await knowledgeFilesApi.list(node.id);setFiles(rows.filter(f=>f.version.mime_type==='video/mp4'));setUncertain(false);setError('已刷新上传列表，请核对并选择固定版本；未自动重新上传文件。');}catch{setError('读取上传结果失败，请保留草稿稍后核对。');}finally{setBusy(false);}}
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await knowledgeFilesApi.upload(
        node.id,
        request.file,
        request.id,
      );
      await onUploaded();
      const rows = await knowledgeFilesApi.list(node.id);
      setFiles(rows.filter((f) => f.version.mime_type === "video/mp4"));
      const f = rows.find(
        (f) => f.id === (result.data as { binding_id?: string }).binding_id,
      );
      if (!f) throw new Error("视频已上传，列表尚未确认");
      setData((d) => ({
        ...d,
        video: {
          binding_id: f.id,
          revision: f.current_revision,
          title: f.title,
        },
      }));
      attempt.current = null;
      setUncertain(false);
    } catch (e) {
      const rejected =
        typeof e === "object" &&
        e !== null &&
        "status" in e &&
        typeof e.status === "number" &&
        e.status >= 400 &&
        e.status < 500;
      if (rejected) attempt.current = null;
      setUncertain(!rejected);
      setError(
        rejected
          ? "上传未成功，请检查文件后重试。"
          : "上传结果未确认，请核对上传结果，避免重复上传。",
      );
    } finally {
      setBusy(false);
    }
  };
  const close = () => {
    if (
      !locked &&
      (JSON.stringify(data) === JSON.stringify(initial) ||
        window.confirm("放弃尚未保存的修改？"))
    )
      onClose();
  };
  const save = async () => {
    if (mode === "video" && data.video !== null && !data.video) {
      setError("请上传或选择一个视频版本");
      return;
    }
    if (mode === "link" && data.link !== null && !safeWebUrl(link.url)) {
      setError("请输入完整的 HTTP 或 HTTPS 网址，不能包含账号密码或空白字符。");
      return;
    }
    if (await onSave(data)) onClose();
  };
  const resolved =
    mode === "link" ? resolveCanvasLink(link.url, link.kind) : null;
  return (
    <Dialog
      motionPreset="canvas"
      open
      title={mode === "video" ? "节点视频" : "网页与视频链接"}
      className="kc-modal"
      onCloseAutoFocus={(e) => {
        e.preventDefault();
        document
          .querySelector<HTMLElement>('[aria-label="知识画布"]')
          ?.focus({ preventScroll: true });
      }}
      onOpenChange={(open) => {
        if (!open) close();
      }}
      closeOnEscape={!locked}
      closeOnOutsideClick={!locked}
      showClose={!locked}
    >
      <div className="kc-form">
        <fieldset disabled={locked}>
          {mode === "video" ? (
            <>
              <label>
                上传原视频
                <input
                  type="file"
                  accept="video/mp4,.mp4"
                  onChange={(e) => void upload(e.target.files?.[0])}
                />
              </label>
              <small>
                MP4，最大 20MB；推荐 H.264/AAC
                编码。上传后在当前页面播放，权限沿用节点。
              </small>
              <label>
                选择固定版本视频
                <select
                  value={
                    data.video
                      ? data.video.binding_id + ":" + data.video.revision
                      : ""
                  }
                  onChange={(e) => {
                    const f = files.find(
                      (f) => f.id === e.target.value.split(":")[0],
                    );
                    if (f)
                      setData({
                        ...data,
                        video: {
                          binding_id: f.id,
                          revision: f.current_revision,
                          title: f.title,
                        },
                      });
                  }}
                >
                  <option value="">选择视频</option>
                  {data.video &&
                    !files.some(
                      (f) =>
                        f.id === data.video?.binding_id &&
                        f.current_revision === data.video.revision,
                    ) && (
                      <option
                        value={
                          data.video.binding_id + ":" + data.video.revision
                        }
                      >
                        当前固定视频版本（保留）
                      </option>
                    )}
                  {files.map((f) => (
                    <option key={f.id} value={f.id + ":" + f.current_revision}>
                      {f.title} · v{f.current_revision}
                    </option>
                  ))}
                </select>
              </label>
              {data.video && (
                <label>
                  显示名称
                  <input
                    value={data.video.title}
                    maxLength={200}
                    onChange={(e) =>
                      setData({
                        ...data,
                        video: { ...data.video!, title: e.target.value },
                      })
                    }
                  />
                </label>
              )}
            </>
          ) : (
            <>
              <label>
                网址
                <input
                  aria-label="节点网址"
                  type="url"
                  value={link.url}
                  maxLength={2048}
                  placeholder="https://…"
                  onChange={(e) =>
                    setData({ ...data, link: { ...link, url: e.target.value } })
                  }
                />
              </label>
              <label>
                显示名称
                <input
                  aria-label="链接显示名称"
                  value={link.title}
                  maxLength={200}
                  onChange={(e) =>
                    setData({
                      ...data,
                      link: { ...link, title: e.target.value },
                    })
                  }
                />
              </label>
              <label>
                打开方式
                <select
                  aria-label="链接打开方式"
                  value={link.kind}
                  onChange={(e) =>
                    setData({
                      ...data,
                      link: {
                        ...link,
                        kind: e.target.value as typeof link.kind,
                      },
                    })
                  }
                >
                  <option value="auto">自动识别视频或网页</option>
                  <option value="video">视频链接（优先预览）</option>
                  <option value="web">普通网页（跳转打开）</option>
                </select>
              </label>
              <small>
                外部链接内容由原网站提供，可能变化；需要固定版本时请上传原文件。
              </small>
              {resolved && (
                <p>
                  {resolved.mode === "embed"
                    ? "将使用 " + resolved.provider + " 官方播放器站内预览"
                    : resolved.mode === "direct"
                      ? "将使用页面内视频播放器"
                      : "将保留原网页跳转入口"}
                </p>
              )}
            </>
          )}
          {initial[mode] && (
            <button onClick={() => setData({ ...data, [mode]: null })}>
              移除{mode === "video" ? "视频" : "链接"}
            </button>
          )}
        </fieldset>
        {(error || saveError) && <p role="alert">{error || saveError}</p>}
        {uncertain && (
          <button disabled={pending || busy} onClick={() => void upload()}>
            核对上传结果
          </button>
        )}
        {unknown && (
          <button disabled={pending || busy} onClick={onCheck}>
            核对保存结果
          </button>
        )}
        <div className="kc-form-actions">
          <button disabled={locked} onClick={() => void save()}>
            保存{mode === "video" ? "视频" : "链接"}
          </button>
          <button disabled={locked} onClick={close}>
            取消
          </button>
        </div>
      </div>
    </Dialog>
  );
}
