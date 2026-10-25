import {DerivedTags} from './DerivedDirectory';
import {boundaryEntries} from './canvasGroups';
import type {GroupSelection,CanvasGroup} from './canvasGroups';
import { summaryPath } from "./groupGeometry";
import { branchDirection } from "./structureDirection";
import { CanvasRangeHandles } from "./CanvasRangeHandles";
import { useEffect, useState } from "react";
import type { CanvasData, KnowledgeNode } from "../../services/knowledge";
import { Dialog } from "../primitives/Dialog";
import { groupBounds } from "./canvasTools";
export type AnnotationMode = "note" | "labels" | "task" | "boundary";
const labels = { note: "备注", labels: "标签", task: "任务", boundary: "外框" };
export type AnnotationDraft={data:CanvasData;tags:string};
export function CanvasAnnotationDialog({
  restored, onDraft,
  node,
  mode,
  pending,
  writable,
  unknown,
  saveError,
  onCheck,
  onSave,
  onClose,
}: {
  restored?:AnnotationDraft;onDraft?:(draft:AnnotationDraft)=>void;
  node: KnowledgeNode;
  mode: AnnotationMode;
  pending: boolean;
  writable: boolean;
  unknown?: boolean;
  saveError?: string;
  onCheck?: () => void;
  onSave: (data: CanvasData) => Promise<boolean>;
  onClose: () => void;
}) {
  const original = node.canvas_data ?? {};
  const [data, setData] = useState<CanvasData>(restored?.data??original),
    [tags, setTags] = useState(restored?.tags??(original.labels ?? []).join("，"));
  useEffect(()=>onDraft?.({data,tags}),[data,tags,onDraft]);
  const task = data.task ?? {
    progress: 0,
    priority: 0,
    start: "",
    due: "",
    assignee: "",
  };
  const [error, setError] = useState("");
  const close = () => {
    if (
      !pending &&
      ((JSON.stringify(data) === JSON.stringify(original) &&
        tags === (original.labels ?? []).join("，")) ||
        !writable ||
        window.confirm("放弃尚未保存的修改？"))
    )
      onClose();
  };
  const save = async (remove = false) => {
    let next = { ...data };
    if (remove) {
      next = { ...original };
      delete next[mode];
    } else if (mode === "labels") {
      const values = [
        ...new Set(
          tags
            .split(/[,，\n]/)
            .map((t) => t.trim())
            .filter(Boolean),
        ),
      ];
      if (values.length > 20 || values.some((t) => t.length > 50)) {
        setError("最多20个标签，每个不超过50字");
        return;
      }
      next.labels = values;
    } else if (mode === "boundary" && !data.boundary?.title.trim()) {
      setError("请输入外框名称");
      return;
    } else if (mode === "task") {
      if (task.start && task.due && task.due < task.start) {
        setError("截止日期不能早于开始日期");
        return;
      }
      next.task = task;
    }
    if (await onSave(next)) onClose();
  };
  return (
    <Dialog
      motionPreset="canvas"
      open
      title={labels[mode] + " · " + node.title}
      onOpenChange={(open) => {
        if (!open) close();
      }}
      closeOnEscape={!pending}
      closeOnOutsideClick={!pending}
      onCloseAutoFocus={(event) => {
        event.preventDefault();
        document
          .querySelector<HTMLElement>('[aria-label="知识画布"]')
          ?.focus({ preventScroll: true });
      }}
      className="kc-modal"
    >
      <div className="kc-form">
        <fieldset disabled={pending || unknown || !writable}>
          {mode === "note" && (
            <label>
              备注内容
              <textarea
                aria-label="备注内容"
                value={data.note ?? ""}
                maxLength={20000}
                onChange={(e) => setData({ ...data, note: e.target.value })}
              />
            </label>
          )}
          {mode === "labels" && (
            <><DerivedTags objectId={node.id} writable={writable&&!pending}/><label>
              标签（逗号分隔）
              <textarea
                aria-label="标签内容"
                value={tags}
                onChange={(e) => setTags(e.target.value)}
              />
            </label></>
          )}
          {mode === "boundary" && (
            <label>
              外框名称
              <input
                aria-label="外框名称"
                maxLength={200}
                value={data.boundary?.title ?? ""}
                onChange={(e) =>
                  setData({
                    ...data,
                    boundary: {
                      members: original.boundary?.members ?? [],
                      title: e.target.value,
                    },
                  })
                }
              />
            </label>
          )}
          {mode === "task" && (
            <>
              <label>
                进度
                <select
                  aria-label="任务进度"
                  value={task.progress}
                  onChange={(e) =>
                    setData({
                      ...data,
                      task: { ...task, progress: +e.target.value },
                    })
                  }
                >
                  {[0, 25, 50, 75, 100].map((v) => (
                    <option key={v} value={v}>
                      {v}%
                    </option>
                  ))}
                </select>
              </label>
              <label>
                优先级
                <select
                  aria-label="任务优先级"
                  value={task.priority}
                  onChange={(e) =>
                    setData({
                      ...data,
                      task: { ...task, priority: +e.target.value },
                    })
                  }
                >
                  {["无", "高", "中", "低"].map((v, i) => (
                    <option key={i} value={i}>
                      {v}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                开始日期
                <input
                  aria-label="任务开始日期"
                  type="date"
                  value={task.start}
                  onChange={(e) =>
                    setData({
                      ...data,
                      task: { ...task, start: e.target.value },
                    })
                  }
                />
              </label>
              <label>
                截止日期
                <input
                  aria-label="任务截止日期"
                  type="date"
                  value={task.due}
                  onChange={(e) =>
                    setData({ ...data, task: { ...task, due: e.target.value } })
                  }
                />
              </label>
              <label>
                负责人
                <input
                  aria-label="任务负责人"
                  maxLength={100}
                  value={task.assignee}
                  onChange={(e) =>
                    setData({
                      ...data,
                      task: { ...task, assignee: e.target.value },
                    })
                  }
                />
              </label>
              <small>
                负责人为任务备注，不授予权限、不发送通知。依赖关系使用关联线“依赖”。
              </small>
            </>
          )}
        </fieldset>
        {(error || saveError) && <p role="alert">{error || saveError}</p>}
        <div className="kc-form-actions">
          {unknown && (
            <button disabled={pending} onClick={onCheck}>
              核对保存结果
            </button>
          )}
          {writable && (
            <>
              <button disabled={pending || unknown} onClick={() => void save()}>
                保存{labels[mode]}
              </button>
              {original[mode] && (
                <button
                  disabled={pending || unknown}
                  onClick={() => void save(true)}
                >
                  移除{labels[mode]}
                </button>
              )}
            </>
          )}
          <button disabled={pending} onClick={close}>
            关闭
          </button>
        </div>
      </div>
    </Dialog>
  );
}
export function CanvasGroupOverlays({nodes,sizes,onSummary,onBoundary,zoom=1,onRange,selected,onSelect,onContext,disabled=false}:{
 nodes:KnowledgeNode[];sizes:Map<string,{width:number;height:number}>;onSummary:(node:KnowledgeNode)=>void;onBoundary:(node:KnowledgeNode,key?:string)=>void;zoom?:number;
 onRange?:(node:KnowledgeNode,kind:'summary'|'boundary',members:string[],key?:string)=>Promise<boolean>;
 selected?:GroupSelection|null;onSelect?:(selection:GroupSelection)=>void;onContext?:(selection:GroupSelection,point:{x:number;y:number})=>void;disabled?:boolean;
}) {
 const [preview,setPreview]=useState<{node:string;kind:'summary'|'boundary';key?:string;members:string[]}|null>(null);
 const entries=nodes.flatMap(node=>[...(node.canvas_data?.summary?[{node,kind:'summary' as const,key:undefined,group:node.canvas_data.summary}]:[]),...boundaryEntries(node.canvas_data).map(b=>({node,kind:'boundary' as const,key:b.key,group:b.group}))]);
 const counts=new Map<string,number>();
 return <>{entries.map(({node,kind,key,group:original})=>{
  const inset=kind==='boundary'?(counts.get(original.members[0])??0)*26:0;if(kind==='boundary')counts.set(original.members[0],(counts.get(original.members[0])??0)+1);
  const selection={node:node.id,kind,key},active=selected?.node===node.id&&selected.kind===kind&&selected.key===key;
  const group:CanvasGroup=preview?.node===node.id&&preview.kind===kind&&preview.key===key?{...original,members:preview.members}:original;
  const bounds=groupBounds(kind==='summary'?nodes.filter(n=>n.id!==node.id):nodes,group.members,sizes);if(!bounds)return null;
  const select=()=>{if(disabled)return;if(onSelect)onSelect(selection);else if(kind==='boundary')onBoundary(node,key);else onSummary(node);};
  const edit=()=>{if(!disabled){if(kind==='boundary')onBoundary(node,key);else onSummary(node);}};
  const context=(e:React.MouseEvent)=>{e.preventDefault();e.stopPropagation();if(!disabled){select();onContext?.(selection,{x:e.clientX,y:e.clientY});}};
  const keys=(e:React.KeyboardEvent)=>{if(['Enter',' '].includes(e.key)){e.preventDefault();e.stopPropagation();select();}};
  const virtual={...node,canvas_data:{...node.canvas_data,[kind]:group}};
  return <div key={`${node.id}:${kind}:${key??''}`} className={'kc-group-overlay'+(active?' selected':'')}>
   {onRange&&(active||selected===undefined)&&<CanvasRangeHandles node={virtual} nodes={nodes} sizes={sizes} zoom={zoom} kind={kind} frameInset={inset} onSave={members=>onRange(node,kind,members,key)} onPreview={members=>setPreview(members?{...selection,members}:null)}/>}
   {kind==='boundary'?<><div role="button" tabIndex={disabled?-1:0} aria-label={'外框 '+group.title} aria-pressed={active} data-group-key={key} className={'kc-boundary'+(active?' selected':'')} style={{left:bounds.x-20-inset,top:bounds.y-30-inset,width:bounds.right-bounds.x+40+inset*2,height:bounds.bottom-bounds.y+50+inset*2}} onPointerDown={e=>{e.stopPropagation();if(e.button===0){e.preventDefault();select();}}} onClick={e=>{e.stopPropagation();select();}} onDoubleClick={e=>{e.stopPropagation();edit();}} onContextMenu={context} onKeyDown={keys}/><button className="kc-boundary-title" style={{left:bounds.x,top:bounds.y-30-inset}} aria-pressed={active} onPointerDown={e=>e.stopPropagation()} onClick={e=>{e.stopPropagation();select();}} onDoubleClick={e=>{e.stopPropagation();edit();}} onContextMenu={context}>{group.title||'外框'}</button></>:<svg className={'kc-summary-bracket'+(active?' selected':'')} aria-label={'概要连接 '+node.title} onPointerDown={e=>e.stopPropagation()}><path role="button" tabIndex={disabled?-1:0} aria-label={'概要括号 '+node.title} aria-pressed={active} onClick={e=>{e.stopPropagation();select();}} onDoubleClick={e=>{e.stopPropagation();edit();}} onContextMenu={context} onKeyDown={keys} d={summaryPath(bounds,branchDirection(nodes,group.members[0]),{x:node.x,y:node.y,...(sizes.get(node.id)??{width:272,height:170})})}/></svg>}
  </div>;
 })}</>;
}
