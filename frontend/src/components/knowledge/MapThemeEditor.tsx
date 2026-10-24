import { Dialog } from "../primitives/Dialog";
import type {
  KnowledgeNode,
  MapTheme,
  CanvasStyle,
} from "../../services/knowledge";
import {
  normalizeMapTheme,
  paletteNames,
  mapPalettes,
  mapAppearance,
  topicDepth,
} from "./mapTheme";
import { defaultStyle, titleStyle, nodeStyle } from "./canvasStyle";
export type MapThemeDraft = {
  container: string;
  revision: number;
  theme: MapTheme;
  reset: boolean;
};
export function MapThemeEditor({
  draft,
  nodes,
  source,
  pending,
  unknown,
  error,
  onChange,
  onSave,
  onClose,
  onCheck,
}: {
  draft: MapThemeDraft;
  nodes: KnowledgeNode[];
  source?: KnowledgeNode;
  pending: boolean;
  unknown: boolean;
  error: string;
  onChange: (value: MapThemeDraft) => void;
  onSave: (level?: { depth: number; style: CanvasStyle }) => Promise<boolean>;
  onClose: () => void;
  onCheck: () => void;
}) {
  const locked = pending || unknown,
    theme = normalizeMapTheme(draft.theme),
    update = (value: Partial<MapTheme>) =>
      onChange({ ...draft, theme: { ...theme, ...value } }),
    depth = source ? topicDepth(nodes, source) : 0;
  const demo: KnowledgeNode[] = [
    {
      id: "root",
      node_id: "root",
      parent_id: null,
      container_id: "preview",
      title: "中心主题",
      kind: "topic",
      revision: 1,
      layout_revision: 1,
      x: 0,
      y: 0,
    },
    ...["项目计划", "执行跟进", "年度总结"].map((title, i) => ({
      id: "p" + i,
      node_id: "p" + i,
      parent_id: "root",
      container_id: "preview",
      title,
      kind: "topic" as const,
      revision: 1,
      layout_revision: 1,
      x: 0,
      y: 0,
      sort_order: i,
    })),
  ];
  return (
    <Dialog
      motionPreset="canvas"
      open
      title="全图样式"
      className="kc-modal"
      closeOnEscape={!locked}
      closeOnOutsideClick={false}
      onOpenChange={(open) => {
        if (!open && !locked) onClose();
      }}
      onCloseAutoFocus={(e) => {
        e.preventDefault();
        document
          .querySelector<HTMLElement>('[aria-label="知识画布"]')
          ?.focus({ preventScroll: true });
      }}
    >
      <div className="kc-form">
        <p>
          作用于当前空间；新建主题继承。节点自定义样式优先，选择覆盖后可统一整图。
        </p>
        <fieldset disabled={locked}>
          <fieldset><legend>自动排布</legend>{([['auto_balance','按左右子树实际高度自动平衡'],['compact','整图紧凑布局'],['equal_width','同层等宽']] as const).map(([key,label])=><label key={key}><input type="checkbox" aria-label={label} checked={theme.layout?.[key]??false} onChange={e=>update({layout:{auto_balance:false,compact:false,equal_width:false,...theme.layout,[key]:e.target.checked}})}/>{label}</label>)}<p>自动平衡作用于左右思维导图；概要与外框范围保持整体。同层等宽保留原有自定义宽度，关闭后恢复。</p></fieldset>
          <label>
            整图配色
            <select
              aria-label="整图配色"
              value={theme.palette}
              onChange={(e) =>
                update({ palette: e.target.value as MapTheme["palette"] })
              }
            >
              {Object.entries(paletteNames).map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <label>
            <input
              type="checkbox"
              checked={theme.background === "auto"}
              onChange={(e) =>
                update({ background: e.target.checked ? "auto" : "#f7f8fa" })
              }
            />
            背景跟随应用
          </label>
          {theme.background !== "auto" && (
            <label>
              画布背景
              <input
                aria-label="画布背景"
                type="color"
                value={theme.background}
                onChange={(e) => update({ background: e.target.value })}
              />
            </label>
          )}
          <label>
            <input
              type="checkbox"
              checked={theme.grid}
              onChange={(e) => update({ grid: e.target.checked })}
            />
            显示点阵网格
          </label>
          <label>
            全局字体
            <select
              aria-label="全局字体"
              value={theme.font}
              onChange={(e) =>
                update({ font: e.target.value as MapTheme["font"] })
              }
            >
              <option value="system">系统字体</option>
              <option value="sans">无衬线</option>
              <option value="serif">衬线</option>
              <option value="mono">等宽</option>
            </select>
          </label>
          <label>
            全局字号
            <input
              aria-label="全局字号"
              type="number"
              min={10}
              max={48}
              value={theme.font_size}
              onChange={(e) =>
                update({
                  font_size: Math.max(
                    10,
                    Math.min(48, Math.round(Number(e.target.value) || 15)),
                  ),
                })
              }
            />
          </label>
          <label>
            <input
              type="checkbox"
              checked={draft.reset}
              onChange={(e) => onChange({ ...draft, reset: e.target.checked })}
            />
            覆盖现有节点自定义样式（保留内容、宽度和文件）
          </label>
          <div
            className="kc-map-preview"
            aria-label="全图样式预览"
            style={{
              background:
                theme.background === "auto" ? "#f7f8fa" : theme.background,
            }}
          >
            {demo.map((n) => {
              const data = mapAppearance(n, demo, theme);
              return (
                <div
                  key={n.id}
                  style={{
                    ...nodeStyle(data),
                    width: "auto",
                    minWidth: 80,
                    borderStyle: "solid",
                    padding: 12,
                  }}
                >
                  <span style={titleStyle(data)}>{n.title}</span>
                </div>
              );
            })}
          </div>
          <div className="kc-map-swatches" aria-hidden="true">
            {mapPalettes[theme.palette].map((color) => (
              <span key={color} style={{ background: color }} />
            ))}
          </div>
          <section>
            <h3>按层级统一更新</h3>
            {source ? (
              <>
                <p>
                  使用「{source.title}」当前显示样式，更新第 {depth + 1} 层的{" "}
                  {nodes.filter((n) => topicDepth(nodes, n) === depth).length}{" "}
                  个主题；清除该层单独样式，新主题继承。
                </p>
                <button
                  type="button"
                  disabled={depth > 31}
                  onClick={() =>
                    void onSave({
                      depth,
                      style: mapAppearance(source, nodes, theme)?.style ?? {
                        ...defaultStyle,
                        bold: true,
                      },
                    })
                  }
                >
                  更新所选节点所在层级
                </button>
              </>
            ) : (
              <p>关闭面板并单选一个节点后，可把它的样式应用到同层级。</p>
            )}
            {theme.levels.length > 0 && (
              <button type="button" onClick={() => update({ levels: [] })}>
                清除层级样式规则
              </button>
            )}
          </section>
          <button
            type="button"
            onClick={() =>
              onChange({ ...draft, theme: normalizeMapTheme(), reset: false })
            }
          >
            恢复默认全图设置
          </button>
        </fieldset>
        {error && <p role="alert">{error}</p>}
        {unknown && <p>上次保存结果尚未确认，请先核对，避免重复应用。</p>}
        <footer>
          {unknown && (
            <button disabled={pending} onClick={onCheck}>
              核对保存结果
            </button>
          )}
          <button disabled={locked} onClick={onClose}>
            取消
          </button>
          <button
            className="primary"
            disabled={locked}
            onClick={() => void onSave()}
          >
            应用全图样式
          </button>
        </footer>
      </div>
    </Dialog>
  );
}
