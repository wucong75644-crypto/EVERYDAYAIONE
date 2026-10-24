import {RichTitle} from './TitleText';
import type {TextMark} from './titleEditing';
import { branchDirection } from "./structureDirection";
import { useEffect, useRef, useState } from "react";
import {
  defaultStyle,
  nodeStyle,
  titleStyle,
  imageAspect,
} from "./canvasStyle";
import { siblingCompare } from "./branchLayout";
import { Dialog } from "../primitives/Dialog";
import { knowledgeFilesApi } from "../../services/knowledge";
import type {
  CanvasData,
  CanvasStyle,
  KnowledgeFile,
  KnowledgeNode,
} from "../../services/knowledge";
export function CanvasNodeImage({
  node,
  onEdit,
  onResize,
  zoom = 1,
  disabled = false,
}: {
  node: KnowledgeNode;
  onEdit: () => void;
  onResize?: (width: number) => void;
  zoom?: number;
  disabled?: boolean;
}) {
  const [preview, setPreview] = useState<number | null>(null);
  const resize = useRef<{ x: number; width: number } | null>(null);
  const image = node.canvas_data?.image;
  const [loaded, setLoaded] = useState({ key: "", src: "", failed: false });
  const key = node.id + ":" + image?.binding_id + ":" + image?.revision;
  const src = loaded.key === key ? loaded.src : "",
    failed = loaded.key === key && loaded.failed;
  useEffect(() => {
    const binding = image?.binding_id,
      revision = image?.revision;
    if (!binding || !revision) return;
    const controller = new AbortController();
    let url = "";
    void knowledgeFilesApi
      .content(node.id, binding, revision, controller.signal)
      .then((blob) => {
        if (controller.signal.aborted) return;
        url = URL.createObjectURL(blob);
        setLoaded({ key, src: url, failed: false });
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setLoaded({ key, src: "", failed: true });
      });
    return () => {
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [node.id, image?.binding_id, image?.revision, key]);
  if (!image) return null;
  return (
    <div className="kc-image-wrap">
      <button
        className="kc-inline-image"
        style={{
          width: preview ?? image.width,
          height: (preview ?? image.width) / (image.aspect ?? 1),
        }}
        onPointerDown={(e) => e.stopPropagation()}
        onClick={onEdit}
        aria-label={"调整图片 " + node.title}
      >
        {src ? (
          <img
            src={src}
            alt={image.alt}
            style={{
              width: preview ?? image.width,
              height: (preview ?? image.width) / (image.aspect ?? 1),
              objectFit: "contain",
            }}
            draggable={false}
          />
        ) : (
          <span>{failed ? "图片无法读取，点击检查" : "图片加载中…"}</span>
        )}
      </button>
      {!disabled && onResize && (
        <button
          className="kc-image-resize"
          aria-label={"拖动调整图片大小 " + node.title}
          onPointerDown={(e) => {
            e.stopPropagation();
            e.preventDefault();
            e.currentTarget.focus({ preventScroll: true });
            resize.current = { x: e.clientX, width: image.width };
            e.currentTarget.setPointerCapture(e.pointerId);
          }}
          onPointerMove={(e) => {
            e.stopPropagation();
            if (resize.current)
              setPreview(
                Math.round(
                  Math.max(
                    40,
                    Math.min(
                      240,
                      resize.current.width +
                        (e.clientX - resize.current.x) / zoom,
                    ),
                  ),
                ),
              );
          }}
          onPointerUp={(e) => {
            e.stopPropagation();
            if (resize.current && preview !== null) onResize(preview);
            resize.current = null;
            setPreview(null);
          }}
          onPointerCancel={(e) => {
            e.stopPropagation();
            resize.current = null;
            setPreview(null);
          }}
          onLostPointerCapture={(e) => {
            e.stopPropagation();
            resize.current = null;
            setPreview(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              e.stopPropagation();
              resize.current = null;
              setPreview(null);
            }
            if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
              e.preventDefault();
              e.stopPropagation();
              onResize(
                Math.max(
                  40,
                  Math.min(
                    240,
                    image.width + (e.key === "ArrowLeft" ? -10 : 10),
                  ),
                ),
              );
            }
          }}
        >
          ↘
        </button>
      )}
    </div>
  );
}
export type VisualMode = "style" | "image" | "summary" | "boundary";
export type VisualDraft={data:CanvasData;alignmentChanged:boolean;textMarks:TextMark[];uploadUnconfirmed?:boolean};
export function CanvasVisualEditor({
  restored,onDraft,
  node,
  inheritedStyle,
  mixedAlign=false,
  nodes,
  mode,
  pending,
  unknown,
  saveError,
  onCheck,
  onSave,
  onClose,
  onUploaded,
}: {
  node: KnowledgeNode;
  restored?:VisualDraft;onDraft?:(draft:VisualDraft)=>void;
  inheritedStyle?: CanvasStyle;
  mixedAlign?:boolean;
  nodes: KnowledgeNode[];
  mode: VisualMode;
  pending: boolean;
  unknown: boolean;
  saveError?: string;
  onCheck: () => void;
  onSave: (data: CanvasData,scope?:{align?:'left'|'center'|'right';textMarks?:TextMark[]}) => Promise<boolean>;
  onClose: () => void;
  onUploaded: () => Promise<unknown>;
}) {
  const [alignmentChanged,setAlignmentChanged]=useState(restored?.alignmentChanged??false),[textMarks,setTextMarks]=useState<TextMark[]>(restored?.textMarks??[]);
  const initial = node.canvas_data ?? {},
    [data, setData] = useState<CanvasData>(restored?.data??initial),
    [files, setFiles] = useState<KnowledgeFile[]>([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [uploadUnconfirmed,setUploadUnconfirmed]=useState(restored?.uploadUnconfirmed??false);
  useEffect(()=>onDraft?.({data,alignmentChanged,textMarks,uploadUnconfirmed}),[data,alignmentChanged,textMarks,uploadUnconfirmed,onDraft]);
  const group = mode === "summary" || mode === "boundary" ? data[mode] : null;
  const first = nodes.find(
    (n) =>
      n.id ===
      (mode === "summary" || mode === "boundary"
        ? initial[mode]?.members[0]
        : undefined),
  );
  const candidates = nodes
    .filter(
      (n) =>
        first &&
        n.parent_id === first.parent_id &&
        n.id !== node.id &&
        !n.canvas_data?.summary &&
        branchDirection(nodes, n.id) === branchDirection(nodes, first.id),
    )
    .sort(siblingCompare);
  // Boundary anchor itself is a valid member.
  if (
    mode === "boundary" &&
    first?.parent_id === node.parent_id &&
    !candidates.some((n) => n.id === node.id)
  )
    candidates.push(node);
  candidates.sort(siblingCompare);
  const style = data.style ?? inheritedStyle ?? defaultStyle;
  const locked = pending || busy || unknown || uploadUnconfirmed;
  useEffect(() => {
    if (mode !== "image") return;
    let live = true;
    void knowledgeFilesApi
      .list(node.id)
      .then((items) => {
        if (live)
          setFiles(
            items.filter((f) =>
              /^image\/(png|jpeg|webp|gif)$/.test(f.version.mime_type),
            ),
          );
      })
      .catch(() => {
        if (live) setError("读取图片失败，请关闭后重试");
      });
    return () => {
      live = false;
    };
  }, [mode, node.id]);
  const close = () => {
    if (
      !locked &&
      (JSON.stringify(data) === JSON.stringify(initial) ||
        window.confirm("放弃尚未保存的修改？"))
    )
      onClose();
  };
  const save = async () => {
    setError("");
    if (mode === "boundary" && !group?.title.trim()) {
      setError("请输入外框名称");
      return;
    }
    if (group && !group.members.length) {
      setError("至少保留一个分支");
      return;
    }
    if (await (mode==='style'&&(alignmentChanged||textMarks.length)?onSave(data,{align:alignmentChanged?data.title_format?.align:undefined,textMarks}):onSave(data))) onClose();
  };
  const upload = async (file?: File) => {
    if (!file) return;
    if (!/^image\/(png|jpeg|webp|gif)$/.test(file.type)) {
      setError("请选择 PNG、JPEG、WebP 或 GIF 图片");
      return;
    }
    setBusy(true);setUploadUnconfirmed(true);
    setError("");
    try {
      const result = await knowledgeFilesApi.upload(
        node.id,
        file,
        crypto.randomUUID(),
      );
      await onUploaded();
      const items = await knowledgeFilesApi.list(node.id);
      setFiles(items.filter((f) => f.version.mime_type.startsWith("image/")));
      const added = items.find(
        (f) => f.id === (result.data as { binding_id?: string }).binding_id,
      );
      if (added) {
        const aspect = await imageAspect(file);
        setData((d) => ({
          ...d,
          image: {
            binding_id: added.id,
            revision: added.current_revision,
            width: 220,
            placement: "top",
            aspect,
            alt: file.name,
          },
        }));
      }
      setUploadUnconfirmed(false);
    } catch {
      setError("上传结果未确认，请先检查文件资料，避免重复上传。");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      motionPreset="canvas"
      open
      title={
        {
          style: "样式",
          image: "节点图片",
          summary: "概要范围",
          boundary: "外框范围",
        }[mode]
      }
      className="kc-modal"
      onOpenChange={(open) => {
        if (!open) close();
      }}
      closeOnEscape={!locked}
      closeOnOutsideClick={!locked}
      onCloseAutoFocus={(e) => {
        e.preventDefault();
        document
          .querySelector<HTMLElement>('[aria-label="知识画布"]')
          ?.focus({ preventScroll: true });
      }}
    >
      <div className="kc-form">
        <fieldset disabled={locked} onChangeCapture={e=>{const mark=(e.target as HTMLElement).dataset.titleMark as TextMark|undefined;if(mark)setTextMarks(old=>old.includes(mark)?old:[...old,mark]);}}>
          {mode === "style" && (
            <>
              <label>节点宽度（像素）<input aria-label="节点宽度" type="number" min={160} max={800} value={data.width??272} onChange={e=>setData(d=>({...d,width:Math.max(160,Math.min(800,Math.round(Number(e.target.value)||272)))}))}/></label><button type="button" onClick={()=>setData(d=>({...d,width:null}))}>恢复默认宽度</button>
              <div className="kc-style-preview" style={nodeStyle({ ...data,style })}>
                <span style={titleStyle({ style })}><RichTitle text={node.title} format={data.style===null?undefined:data.title_format}/></span>
              </div>
              {(["color", "background", "border"] as const).map((key, i) => (
                <label key={key}>
                  {["文字颜色", "背景颜色", "边框颜色"][i]}
                  <input
                    aria-label={["文字颜色", "背景颜色", "边框颜色"][i]}
                    type="color"
                    data-title-mark={key==='color'?'color':undefined}
                  value={style[key]}
                    onChange={(e) =>
                      setData({
                        ...data,
                        style: { ...style, [key]: e.target.value },
                      })
                    }
                  />
                </label>
              ))}
              <label>
                字号
                <input
                  type="number"
                  min={10}
                  max={48}
                  data-title-mark="font_size"
                  value={style.font_size}
                  onChange={(e) =>
                    setData({
                      ...data,
                      style: {
                        ...style,
                        font_size: Math.max(
                          10,
                          Math.min(48, Number(e.target.value)),
                        ),
                      },
                    })
                  }
                />
              </label>
              <label>
                <input
                  type="checkbox"
                  data-title-mark="bold"
                  checked={style.bold}
                  onChange={(e) =>
                    setData({
                      ...data,
                      style: { ...style, bold: e.target.checked },
                    })
                  }
                />
                加粗
              </label>
              <label>
                <input
                  type="checkbox"
                  data-title-mark="italic"
                  checked={style.italic}
                  onChange={(e) =>
                    setData({
                      ...data,
                      style: { ...style, italic: e.target.checked },
                    })
                  }
                />
                斜体
              </label>
              <label>
                字体
                <select
                  value={style.font ?? "system"}
                  onChange={(e) =>
                    setData({
                      ...data,
                      style: {
                        ...style,
                        font: e.target.value as CanvasStyle["font"],
                      },
                    })
                  }
                >
                  <option value="system">系统字体</option>
                  <option value="sans">无衬线</option>
                  <option value="serif">衬线</option>
                  <option value="mono">等宽</option>
                </select>
              </label>
              {(["underline", "strike"] as const).map((key, i) => (
                <label key={key}>
                  <input
                    type="checkbox"
                    checked={style[key] ?? false}
                    onChange={(e) =>
                      setData({
                        ...data,
                        style: { ...style, [key]: e.target.checked },
                      })
                    }
                  />
                  {["下划线", "删除线"][i]}
                </label>
              ))}
              <label>
                边框粗细
                <input
                  type="number"
                  min={1}
                  max={6}
                  value={style.border_width ?? 1}
                  onChange={(e) =>
                    setData({
                      ...data,
                      style: {
                        ...style,
                        border_width: Math.max(1, Math.min(6, +e.target.value)),
                      },
                    })
                  }
                />
              </label>
              <label>
                边框线型
                <select
                  value={style.border_style ?? "solid"}
                  onChange={(e) =>
                    setData({
                      ...data,
                      style: {
                        ...style,
                        border_style: e.target
                          .value as CanvasStyle["border_style"],
                      },
                    })
                  }
                >
                  <option value="solid">实线</option>
                  <option value="dashed">虚线</option>
                  <option value="dotted">点线</option>
                </select>
              </label>
              <label>
                形状
                <select
                  value={style.shape}
                  onChange={(e) =>
                    setData({
                      ...data,
                      style: {
                        ...style,
                        shape: e.target.value as CanvasStyle["shape"],
                      },
                    })
                  }
                >
                  <option value="rounded">圆角矩形</option>
                  <option value="rectangle">矩形</option>
                  <option value="pill">胶囊</option>
                </select>
              </label>
              <label>标题对齐<select aria-label="所选主题标题对齐" value={mixedAlign&&!alignmentChanged?'mixed':data.title_format?.align??'left'} onChange={e=>{setAlignmentChanged(true);setData({...data,title_format:{runs:data.title_format?.runs??[],align:e.target.value as 'left'|'center'|'right'}});}}><option value="mixed" disabled>混合</option><option value="left">左对齐</option><option value="center">居中</option><option value="right">右对齐</option></select></label>
              <button onClick={() => setData({ ...data, style: null })}>
                恢复默认样式
              </button>
            </>
          )}
          {group && (
            <>
              <p>
                选择同一父主题、同一排列方向的分支，包含其全部后代；不改变原父子关系。
              </p>
              {mode === "boundary" && (
                <label>
                  外框名称
                  <input
                    value={group.title}
                    maxLength={200}
                    onChange={(e) =>
                      setData({
                        ...data,
                        [mode]: { ...group, title: e.target.value },
                      })
                    }
                  />
                </label>
              )}
              <div className="kc-range-options">
                {candidates.map((n) => (
                  <label key={n.id}>
                    <input
                      type="checkbox"
                      checked={group.members.includes(n.id)}
                      onChange={(e) =>
                        setData({
                          ...data,
                          [mode]: {
                            ...group,
                            members: e.target.checked
                              ? [...group.members, n.id]
                              : group.members.filter((id) => id !== n.id),
                          },
                        })
                      }
                    />
                    {n.title}
                  </label>
                ))}
              </div>
            </>
          )}
          {mode === "image" && (
            <>
              <label>
                上传图片
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/webp,image/gif"
                  onChange={(e) => void upload(e.target.files?.[0])}
                />
              </label>
              <label>
                固定版本图片
                <select
                  value={
                    data.image
                      ? data.image.binding_id + ":" + data.image.revision
                      : ""
                  }
                  onChange={(e) => {
                    const f = files.find(
                      (f) => f.id === e.target.value.split(":")[0],
                    );
                    if (f) {
                      setBusy(true);
                      void knowledgeFilesApi
                        .content(node.id, f.id, f.current_revision)
                        .then(async (blob) => {
                          const aspect = await imageAspect(blob);
                          setData((d) => ({
                            ...d,
                            image: {
                              binding_id: f.id,
                              revision: f.current_revision,
                              width: 220,
                              placement: "top",
                              aspect,
                              alt: f.title,
                            },
                          }));
                        })
                        .catch(() => setError("读取图片失败"))
                        .finally(() => setBusy(false));
                    }
                  }}
                >
                  <option value="">选择图片</option>
                  {data.image &&
                    !files.some(
                      (f) =>
                        f.id === data.image?.binding_id &&
                        f.current_revision === data.image.revision,
                    ) && (
                      <option
                        value={
                          data.image.binding_id + ":" + data.image.revision
                        }
                      >
                        当前固定版本（保留）
                      </option>
                    )}
                  {files.map((f) => (
                    <option key={f.id} value={f.id + ":" + f.current_revision}>
                      {f.title} · v{f.current_revision}
                    </option>
                  ))}
                </select>
              </label>
              {data.image && (
                <>
                  <label>
                    图片宽度
                    <input
                      aria-label="图片宽度"
                      type="range"
                      min={40}
                      max={240}
                      value={data.image.width}
                      onChange={(e) =>
                        setData({
                          ...data,
                          image: { ...data.image!, width: +e.target.value },
                        })
                      }
                    />
                    {data.image.width}px
                  </label>
                  <label>
                    位置
                    <select
                      value={data.image.placement}
                      onChange={(e) =>
                        setData({
                          ...data,
                          image: {
                            ...data.image!,
                            placement: e.target.value as "top" | "bottom",
                          },
                        })
                      }
                    >
                      <option value="top">标题上方</option>
                      <option value="bottom">标题下方</option>
                    </select>
                  </label>
                  <button onClick={() => setData({ ...data, image: null })}>
                    移除节点图片
                  </button>
                </>
              )}
            </>
          )}
        </fieldset>
        {(error || saveError) && <p role="alert">{error || saveError}</p>}
        {uploadUnconfirmed&&<button disabled={pending||busy} onClick={()=>{setBusy(true);void onUploaded().then(()=>knowledgeFilesApi.list(node.id)).then(items=>{setFiles(items.filter(f=>f.version.mime_type.startsWith('image/')));setUploadUnconfirmed(false);setError('已刷新文件列表，请核对并选择固定版本；未自动重新上传图片。');}).catch(()=>setError('读取上传结果失败，请保留草稿稍后核对。')).finally(()=>setBusy(false));}}>核对图片上传结果</button>}
        {unknown && (
          <button disabled={pending} onClick={onCheck}>
            核对保存结果
          </button>
        )}
        <div className="kc-form-actions">
          <button disabled={locked} onClick={() => void save()}>
            保存修改
          </button>
          <button disabled={locked} onClick={close}>
            取消
          </button>
        </div>
      </div>
    </Dialog>
  );
}
