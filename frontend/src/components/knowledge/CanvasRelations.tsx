import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { PointerEvent, RefObject } from "react";
import type {
  KnowledgeNode,
  KnowledgeRelation,
  RelationAppearance,
} from "../../services/knowledge";
import { relationCurve, relationLane, relationTypes, nearestAnchor, stepAnchor } from "./relationGeometry";
export type RelationLabelDraft={relation:KnowledgeRelation;text:string;containerRevision?:number};
type Props = {
  labelDraft?:RelationLabelDraft|null;onLabelDraft?:(draft:RelationLabelDraft|null)=>void;labelWritable?:boolean;
  nodes: KnowledgeNode[];
  relations: KnowledgeRelation[];
  viewport: RefObject<HTMLDivElement | null>;
  selected: string | null;
  writable: boolean;
  disabled: boolean;
  zoom: number;
  dragging: string | null;
  entering?: Set<string>;
  measurements?: Record<string, { width: number; height: number }>;
  onAppearance?: (
    relation: KnowledgeRelation,
    appearance: RelationAppearance,
  ) => Promise<boolean> | boolean | void;
  onSelect: (id: string) => void;
  onReconnect: (
    relation: KnowledgeRelation,
    endpoint: "source" | "target",
    event: PointerEvent<SVGCircleElement>,
  ) => void;
};
export function CanvasRelations({
  labelDraft,onLabelDraft,labelWritable,
  nodes,
  relations,
  viewport,
  selected,
  writable,
  disabled,
  zoom,
  dragging,
  entering,
  measurements,
  onSelect,
  onReconnect,
  onAppearance,
}: Props) {
  const svg=useRef<SVGSVGElement|null>(null);
  const [bendPreview, setBendPreview] = useState<{
    id: string;
    bend: number;
  } | null>(null);
  const [geometry,setGeometry]=useState<{id:string;appearance:RelationAppearance}|null>(null);
  const [localLabelEdit,setLabelEdit]=useState<string|null>(null);
  const labelEdit=onLabelDraft?labelDraft?.relation.id??null:localLabelEdit;
  const openLabel=(r:KnowledgeRelation)=>{setLabelEdit(r.id);onLabelDraft?.({relation:r,text:r.appearance?.text??''});};
  const closeLabel=()=>{setLabelEdit(null);onLabelDraft?.(null);};
  const [sizes, setSizes] = useState<
    Record<string, { width: number; height: number }>
  >({});
  const nodeIdentity = nodes.map((n) => n.id).join(",");
  useLayoutEffect(() => {
    const elements = [
      ...(viewport.current?.querySelectorAll<HTMLElement>("[data-node-id]") ??
        []),
    ];
    const measure = () => {
      const next = Object.fromEntries(
        elements.map((el) => [
          el.dataset.nodeId!,
          { width: el.offsetWidth || 272, height: el.offsetHeight || 170 },
        ]),
      );
      setSizes((old) =>
        JSON.stringify(old) === JSON.stringify(next) ? old : next,
      );
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    elements.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [nodeIdentity, viewport]);
  useEffect(() => {
    const cancel = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setBendPreview(null);
        setGeometry(null);
        svg.current
          ?.querySelectorAll<SVGElement>("[data-drag]")
          .forEach((el) => delete el.dataset.drag);
        if(labelWritable!==false){setLabelEdit(null);onLabelDraft?.(null);}
      }
    };
    window.addEventListener("keydown", cancel);
    return () => window.removeEventListener("keydown", cancel);
  }, [viewport,onLabelDraft,labelWritable]);
  const saveKey=(relation:KnowledgeRelation,appearance:RelationAppearance,target:SVGElement)=>{
    const label=target.getAttribute('aria-label');
    void Promise.resolve(onAppearance?.(relation,appearance)).then(()=>requestAnimationFrame(()=>{
      if(!svg.current || !label || (document.activeElement!==document.body && document.activeElement!==target))return;
      [...svg.current.querySelectorAll<SVGElement>('[aria-label]')].find(el=>el.getAttribute('aria-label')===label)?.focus({preventScroll:true});
    }));
  };
  const index = new Map(nodes.map((n) => [n.id, n]));
  return (
    <svg ref={svg} className="kc-edges kc-associations" aria-label="关联线">
      {relations.map((r) => {
        const a = index.get(r.source_object_id),
          b = index.get(r.target_object_id);
        if (!a || !b) return null;
        const lane = relationLane(relations, r, zoom);
        const appearance=geometry?.id===r.id?geometry.appearance:r.appearance;
        const curve = relationCurve(
          {
            ...a,
            ...(measurements?.[a.id] ??
              sizes[a.id] ?? { width: 272, height: 170 }),
          },
          {
            ...b,
            ...(measurements?.[b.id] ??
              sizes[b.id] ?? { width: 272, height: 170 }),
          },
          lane.offset +
            (bendPreview?.id === r.id
              ? bendPreview.bend
              : (r.appearance?.bend ?? 0)),
          1,appearance,
        );
        const active = selected === r.id;
        return (
          <g
            key={r.id}
            data-relation-id={r.id}
            className={
              "kc-relation" +
              (entering?.has(r.source_object_id) ||
              entering?.has(r.target_object_id)
                ? " kc-edge-enter"
                : "") +
              (active ? " selected" : "") +
              (dragging === r.id ? " dragging" : "")
            }
          >
            <path
              className="kc-relation-hit"
              d={curve.path}
              role="button"
              aria-label={`关联线：${a.title} → ${b.title}${lane.count > 1 ? `（${relationTypes[r.relation_type] ?? "关联"}）` : ""}`}
              aria-pressed={active}
              tabIndex={disabled ? -1 : 0}
              onPointerDown={(e) => {
                e.stopPropagation();
                if (e.button === 0 && !disabled) {
                  e.preventDefault();
                  onSelect(r.id);
                }
              }}
              onClick={(e) => {
                e.stopPropagation();
                if (!disabled) onSelect(r.id);
              }}
              onDoubleClick={(e) => e.stopPropagation()}
              onContextMenu={(e) => {
                e.preventDefault();
                e.stopPropagation();
                if (!disabled) onSelect(r.id);
              }}
              onKeyDown={(e) => {
                if (!disabled && (e.key === "Enter" || e.key === " ")) {
                  e.preventDefault();
                  e.stopPropagation();
                  onSelect(r.id);
                }
              }}
            />
            <path className="association" d={curve.path} aria-hidden="true" />
            {(r.appearance?.text || active) && (
              <text
                className="kc-relation-label"
                x={curve.middle.x}
                y={curve.middle.y - 12}
                textAnchor="middle"
                role="button"
                tabIndex={disabled ? -1 : 0}
                aria-label="编辑关联线文字"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation();
                  if (writable && !disabled) {
                    onSelect(r.id);
                    openLabel(r);
                  }
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && writable && !disabled) {
                    e.preventDefault();
                    e.stopPropagation();
                    onSelect(r.id);
                    openLabel(r);
                  }
                }}
              >
                {r.appearance?.text || "添加文字"}
              </text>
            )}
            {active && writable && !disabled && !r.appearance?.source_control && !r.appearance?.target_control && (
              <circle
                className="kc-relation-bend"
                cx={curve.middle.x}
                cy={curve.middle.y}
                r={7 / zoom}
                role="slider"
                tabIndex={0}
                aria-label="关联线弯曲"
                aria-valuemin={-2000}
                aria-valuemax={2000}
                aria-valuenow={r.appearance?.bend ?? 0}
                onKeyDown={(e) => {
                  if (["ArrowUp", "ArrowDown", "Home"].includes(e.key)) {
                    e.preventDefault();
                    e.stopPropagation();
                    onAppearance?.(r, {
                      ...r.appearance,
                      text: r.appearance?.text ?? "",
                      bend:
                        e.key === "Home"
                          ? 0
                          : Math.max(
                              -2000,
                              Math.min(
                                2000,
                                (r.appearance?.bend ?? 0) +
                                  (e.key === "ArrowUp" ? -20 : 20),
                              ),
                            ),
                    });
                  }
                }}
                onPointerDown={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  e.currentTarget.setPointerCapture(e.pointerId);
                  const normal = curve.normal;
                  e.currentTarget.dataset.drag = JSON.stringify({
                    x: e.clientX,
                    y: e.clientY,
                    bend: r.appearance?.bend ?? 0,
                    nx: normal.x,
                    ny: normal.y,
                  });
                }}
                onPointerMove={(e) => {
                  const raw = e.currentTarget.dataset.drag;
                  if (!raw) return;
                  const d = JSON.parse(raw);
                  setBendPreview({
                    id: r.id,
                    bend: Math.max(
                      -2000,
                      Math.min(
                        2000,
                        d.bend +
                          ((e.clientX - d.x) * d.nx +
                            (e.clientY - d.y) * d.ny) /
                            zoom /
                            0.75,
                      ),
                    ),
                  });
                }}
                onPointerUp={(e) => {
                  e.stopPropagation();
                  if (!e.currentTarget.dataset.drag) return;
                  delete e.currentTarget.dataset.drag;
                  if (bendPreview?.id === r.id)
                    onAppearance?.(r, {
                      ...r.appearance,
                      text: r.appearance?.text ?? "",
                      bend: bendPreview.bend,
                    });
                  setBendPreview(null);
                }}
                onPointerCancel={(e) => {
                  e.stopPropagation();
                  delete e.currentTarget.dataset.drag;
                  setBendPreview(null);
                }}
                onLostPointerCapture={(e) => {
                  e.stopPropagation();
                  delete e.currentTarget.dataset.drag;
                  setBendPreview(null);
                }}
              />
            )}
            {active && writable && !disabled && (['source','target'] as const).map(endpoint=>{
              const port=endpoint==='source'?curve.start:curve.end,control=endpoint==='source'?curve.control1:curve.control2,key=endpoint==='source'?'source_control':'target_control';
              const base:RelationAppearance={text:'',bend:0,...r.appearance};
              const update=(dx:number,dy:number)=>({...base,[key]:{x:Math.max(-4000,Math.min(4000,dx)),y:Math.max(-4000,Math.min(4000,dy))}});
              return <g key={key}><path className="kc-control-guide" d={`M ${port.x} ${port.y} L ${control.x} ${control.y}`}/><circle className="kc-relation-control" cx={control.x} cy={control.y} r={7/zoom} role="button" tabIndex={0} aria-label={'调整关联线'+(endpoint==='source'?'起点':'终点')+'控制点'}
                onPointerDown={e=>{if(e.button!==0)return;e.preventDefault();e.stopPropagation();e.currentTarget.setPointerCapture(e.pointerId);e.currentTarget.dataset.drag=JSON.stringify({x:e.clientX,y:e.clientY,dx:control.x-port.x,dy:control.y-port.y});}}
                onPointerMove={e=>{const raw=e.currentTarget.dataset.drag;if(!raw)return;const d=JSON.parse(raw);setGeometry({id:r.id,appearance:update(d.dx+(e.clientX-d.x)/zoom,d.dy+(e.clientY-d.y)/zoom)});}}
                onPointerUp={e=>{const raw=e.currentTarget.dataset.drag;if(!raw)return;e.stopPropagation();const d=JSON.parse(raw);delete e.currentTarget.dataset.drag;const next=update(d.dx+(e.clientX-d.x)/zoom,d.dy+(e.clientY-d.y)/zoom);if(e.clientX!==d.x||e.clientY!==d.y)void onAppearance?.(r,next);setGeometry(null);}}
                onPointerCancel={e=>{delete e.currentTarget.dataset.drag;setGeometry(null);}} onLostPointerCapture={e=>{delete e.currentTarget.dataset.drag;setGeometry(null);}}
                onKeyDown={e=>{if(e.key==='Home'){e.preventDefault();e.stopPropagation();saveKey(r,{...base,[key]:null},e.currentTarget);}else if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();e.stopPropagation();const step=e.shiftKey?20:5;saveKey(r,update(control.x-port.x+(e.key==='ArrowRight'?step:e.key==='ArrowLeft'?-step:0),control.y-port.y+(e.key==='ArrowDown'?step:e.key==='ArrowUp'?-step:0)),e.currentTarget);}}}/></g>;
            })}
            {labelEdit === r.id && active && (
              <RelationLabelEditor
                key={r.id + ":" + r.appearance?.text}
                relation={r}
                textDraft={labelDraft?.relation.id===r.id?labelDraft.text:undefined}
                onDraft={text=>onLabelDraft?.({relation:labelDraft?.relation??r,text,containerRevision:labelDraft?.containerRevision})}
                disabled={labelWritable===undefined?!writable||disabled:!labelWritable}
                x={curve.middle.x}
                y={curve.middle.y}
                onSave={async (text) => {
                  const ok = await onAppearance?.(r, {
                    ...r.appearance,
                    text,
                    bend: r.appearance?.bend ?? 0,
                  });
                  if (ok !== false) closeLabel();
                  return ok !== false;
                }}
                onClose={closeLabel}
              />
            )}

            {active &&
              writable &&
              !disabled &&
              (["source", "target"] as const).map((endpoint) => {
                const port = endpoint === "source" ? curve.start : curve.end;
                return (
                  <circle
                    key={endpoint}
                    className="kc-relation-endpoint"
                    cx={port.x + (port.nx * 9) / zoom}
                    cy={port.y + (port.ny * 9) / zoom}
                    r={7 / zoom}
                    aria-label={`拖动关联线${endpoint === "source" ? "起点" : "终点"}重新连接`}
                    role="button" tabIndex={0}
                    onKeyDown={e=>{
                      if(!['Home','ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key))return;
                      e.preventDefault();e.stopPropagation();const n=endpoint==='source'?a:b,box={...n,...(measurements?.[n.id]??sizes[n.id]??{width:272,height:170})};
                      const key=endpoint==='source'?'source_anchor':'target_anchor',anchor=nearestAnchor(box,port);
                      saveKey(r,{text:'',bend:0,...r.appearance,[key]:e.key==='Home'?null:stepAnchor(box,anchor,(e.key==='ArrowLeft'||e.key==='ArrowUp'?-1:1)*(e.shiftKey?20:5))},e.currentTarget);
                    }}
                    onPointerDown={(e) => {
                      e.stopPropagation();
                      e.preventDefault();
                      if (e.button === 0) onReconnect(r, endpoint, e);
                    }}
                    onClick={(e) => e.stopPropagation()}
                    onDoubleClick={(e) => e.stopPropagation()}
                    onContextMenu={(e) => {
                      e.stopPropagation();
                      e.preventDefault();
                    }}
                  />
                );
              })}
          </g>
        );
      })}
    </svg>
  );
}

function RelationLabelEditor({
  textDraft,onDraft,
  relation,
  x,
  y,
  onSave,
  onClose,
  disabled,
}: {
  textDraft?:string;onDraft?:(text:string)=>void;
  relation: KnowledgeRelation;
  disabled: boolean;
  x: number;
  y: number;
  onSave: (text: string) => Promise<boolean>;
  onClose: () => void;
}) {
  const [text, setText] = useState(textDraft??relation.appearance?.text ?? ""),
    [error, setError] = useState("");
  const save = async () => {
    if (!disabled && !(await onSave(text)))
      setError("保存未完成，请保留输入；结果未知时通过顶部刷新核对。");
  };
  return (
    <foreignObject
      x={x - 130}
      y={y + 16}
      width={300}
      height={140}
      style={{ pointerEvents: "auto" }}
      onPointerDown={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === "Escape") onClose();
        if (e.key === "Enter") void save();
      }}
    >
      <div className="kc-relation-input">
        <input
          autoFocus
          disabled={disabled}
          aria-label="关联线文字"
          value={text}
          maxLength={200}
          onChange={(e) => {setText(e.target.value);onDraft?.(e.target.value);}}
        />
        <button disabled={disabled} onClick={() => void save()}>
          保存
        </button>
        <button disabled={disabled} onClick={onClose}>
          取消
        </button>
        {error && <small role="alert">{error}</small>}
      </div>
    </foreignObject>
  );
}
