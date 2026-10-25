import {quickPlacement} from '../components/knowledge/quickPlacement';
import {AttachmentSummary,BoundFilePreview,NodeFileMenu} from '../components/knowledge/KnowledgeAttachmentUI';
import type {KnowledgeFileVersion} from '../services/knowledge';
import ImagePreviewModal from '../components/chat/media/ImagePreviewModal';
import {InlineBodyEditor} from '../components/knowledge/InlineBodyEditor';
import {manualTopicPath} from '../components/knowledge/manualLayout';
import type {ManualLayout} from '../services/knowledge';
import {canvasSafeArea,canvasObstacles,fitSafeView,revealScreenRect} from '../components/knowledge/canvasOcclusion';
import {useCanvasOcclusion} from '../components/knowledge/useCanvasOcclusion';
import {useCanvasEditingVisibility} from '../components/knowledge/useCanvasEditingVisibility';
import {useMeasuredSurface} from '../components/knowledge/useMeasuredSurface';
import type {RelationLabelDraft} from '../components/knowledge/CanvasRelations';
import {followSummaryPositions} from '../components/knowledge/summaryLayout';
import {branchScope,nodeHidden,reconcileSummaries,summaryParent} from '../components/knowledge/summaryScope';
import type {AnnotationDraft} from '../components/knowledge/CanvasAnnotations';
import type {VisualDraft} from '../components/knowledge/CanvasVisualEditor';
import type {MediaDraft} from '../components/knowledge/CanvasMedia';
import {displayedTitleStyle} from '../components/knowledge/titleEditing';
import {appendBoundary,selectedGroup as readGroup,setBoundary,boundaryEntries} from '../components/knowledge/canvasGroups';
import type {GroupSelection} from '../components/knowledge/canvasGroups';
import {editTopicStyle,pasteTopicStyle,styleSnapshot} from '../components/knowledge/textScope';
import type {StyleSnapshot} from '../components/knowledge/textScope';
import {defaultStyle} from '../components/knowledge/canvasStyle';
import {RichTitle} from '../components/knowledge/TitleText';
import {editRuns,paintTitle,richFont} from '../components/knowledge/richTitle';
import {RichTitleEditor} from '../components/knowledge/RichTitleEditor';
import type {TitleEditorHandle} from '../components/knowledge/RichTitleEditor';
import type {TitleFormat} from '../services/knowledge';
import {MapThemeEditor} from '../components/knowledge/MapThemeEditor';
import type {MapThemeDraft} from '../components/knowledge/MapThemeEditor';
import {mapAppearance,mapNodeStyle,mapBranchColor,normalizeMapTheme,topicDepth,updateMapLevel} from '../components/knowledge/mapTheme';
import {smartGuides} from '../components/knowledge/smartGuides';
import {SmartGuideOverlay} from '../components/knowledge/SmartGuideOverlay';
import {branchFoldState,visibleSelection,centerTopic,centerTopicView} from '../components/knowledge/branchBrowsing';
import {TopicWidthHandle,type TopicResize} from '../components/knowledge/TopicWidthHandle';
import {topicWidth,topicSize,titleHeight,editSelection,titleCaretAt,reorderTopic,removeTopic} from '../components/knowledge/topicEditing';
import {flushSync} from 'react-dom';
import {parseTopicOutline,planTopicOutline,planParentTopic} from '../components/knowledge/topicEntry';
import {portStyle,foldStyle,portDirection,edgePoint,vectors,branchDirection,toolsPosition} from '../components/knowledge/structureDirection';
import {placeSummary} from '../components/knowledge/groupGeometry';
import {planStructuredPaste} from '../components/knowledge/structuredPaste';
import { CanvasMediaCard, CanvasMediaEditor, CanvasMediaPreview } from '../components/knowledge/CanvasMedia';
import { topicStructures, arrangeStructure, structureRoot, structureMoves, effectiveStructure } from '../components/knowledge/topicStructure';
import type { TopicStructure } from '../components/knowledge/topicStructure';
import { titleStyle, imageAspect, visualSizeChanged } from '../components/knowledge/canvasStyle';
import { CanvasNodeImage, CanvasVisualEditor } from '../components/knowledge/CanvasVisualEditor';
import type { VisualMode } from '../components/knowledge/CanvasVisualEditor';
import * as ToolMenu from '@radix-ui/react-dropdown-menu';
import { CanvasAnnotationDialog, CanvasGroupOverlays } from '../components/knowledge/CanvasAnnotations';
import type { AnnotationMode } from '../components/knowledge/CanvasAnnotations';
import { selectedGroups, groupBounds } from '../components/knowledge/canvasTools';
import { FileText, Undo2, Redo2, Copy, Scissors, ClipboardPaste, Trash2, ChevronsLeftRight, RefreshCw, Braces, SquareDashed, StickyNote, ListTodo, Plus, GitBranch, CircleHelp, Video, Link2, Palette, Paintbrush, FileImage, FoldVertical, UnfoldVertical, LocateFixed } from 'lucide-react';
import { placeContent, reflowContent } from '../components/knowledge/contentLayout';
import { CanvasSurfaceMotion } from '../components/knowledge/CanvasSurfaceMotion';
import { canvasSnapshot, useCanvasPresence } from '../components/knowledge/useCanvasPresence';
import { useCanvasReducedMotion, useCanvasViewMotion, useCanvasNodeMotion } from '../components/knowledge/useCanvasMotion';
import { CanvasContextMenu, CanvasMenuItem, CanvasSubMenu } from '../components/knowledge/CanvasContextMenu';
import { relationCurve, relationTypes, relationLane, curveBounds } from '../components/knowledge/relationGeometry';
import { CanvasRelations } from '../components/knowledge/CanvasRelations';
import { useCanvasDraft } from '../components/knowledge/useCanvasDraft';
import { historyTargets } from '../components/knowledge/historyTargets';
import { useCanvasEdgePan } from '../components/knowledge/useCanvasEdgePan';
import { wheelView, zoomAt } from '../components/knowledge/viewportGestures';
import { navigateNode, revealNode } from '../components/knowledge/canvasNavigation';
import { planBranch, planBranches, siblingCompare } from '../components/knowledge/branchLayout';
import type { BranchPlan, CardSize } from '../components/knowledge/branchLayout';
import { useCanvasConnection } from '../components/knowledge/useCanvasConnection';
import { KnowledgeSource } from '../components/knowledge/KnowledgeSource';
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { CSSProperties, PointerEvent as ReactPointerEvent } from 'react';
import { Link } from 'react-router-dom';
import { useAuthStore } from '../stores/useAuthStore';
import { knowledgeApi, knowledgeFilesApi } from '../services/knowledge';
import type { CanvasData, CanvasHistory, KnowledgeScene, KnowledgeNode, KnowledgeObject, KnowledgeScope, KnowledgeMember, KnowledgeRole } from '../services/knowledge';
import './KnowledgeCanvas.css';
import type {FileEntryAction} from '../components/knowledge/KnowledgeFiles';
import { KnowledgeFiles } from '../components/knowledge/KnowledgeFiles';
import { KnowledgeHistory } from '../components/knowledge/KnowledgeHistory';
import { KnowledgeSearch } from '../components/knowledge/KnowledgeSearch';
import { Dialog } from '../components/primitives/Dialog';
const noHistory: CanvasHistory = { undo: null, redo: null };
const createsNode=(operation:string)=>operation.startsWith('create_')||operation==='insert_parent';
const historyLabels: Record<string, string> = { create_relation_topic:'浮动主题与关联线',update_map_theme:'全图样式',delete_topic:'删除主题（保留子主题）',create_topics:'批量创建主题',insert_parent:'插入父主题', create_template:'创建主题', update_relation: '关联线文字与弯曲', update_canvas_data: '排列、节点信息与样式', create_summaries: '创建概要', create_project: '创建项目', create_object: '创建节点', update_title: '改名', connect: '连线', delete_relation: '删除关联线', reconnect_relation: '重连关联线', move_branches: '批量移动分支', move_branch: '分支调整', reparent: '层级调整', move_nodes: '移动', copy_branches: '粘贴分支', delete_branches: '删除分支' };
const empty: KnowledgeScene = { containers: [], nodes: [], relations: [] };
const scopes = { personal: '个人知识库', project: '项目', team: '团队知识库' };
const kinds = { topic: '主题', document: '文档', rule: '规则', project: '新项目' };
type QuickDraft = { manualLayout?:ManualLayout; standalone?:boolean; titleFormat?:TitleFormat; wrap?:KnowledgeNode; structure?: TopicStructure; node: KnowledgeNode | null; container: string; parent: string | null; position: Point; title: string; fromLine?: boolean; fixedPosition?: boolean; lineSource?: string; linePort?: 'child'|'sibling'; after?: string; before?: string };
type Point = { x: number; y: number };
type Draft = { created?: KnowledgeNode; type: keyof typeof kinds; container: string; parent: string | null; title: string; body: string; position: Point };
type Menu = { group?:GroupSelection; x: number; y: number; point: Point; parent: string | null };
type BranchClipboard = { identity: string; container: string; revision: number; objects: { object_id: string; expected_revision: number }[]; mode?: 'copy' | 'cut'; token?: string; snapshot_id?: string; snapshot_nodes?: KnowledgeNode[] };
type Drag = { type: 'pan'; start: Point; pan: Point } | { type: 'node'; snapDisabled?:boolean; snapLocks?:{x?:string;y?:string}; id: string; sources: string[]; identity: string; remeasure?: boolean; cursor?: Point; origin: Point; foldedBase: Set<string>; hover?: { id: string; since: number }; start: Point; position: Point; moved?: boolean; base: KnowledgeScene; sizes: Map<string, CardSize>; visible: Set<string>; pointer: number; offsets?: Map<string, Point>; plan?: BranchPlan };
function message(e: unknown) { return e instanceof Error ? e.message : '操作失败，请重试'; }
export default function KnowledgeCanvas() {
  const mac = /Mac|iPhone|iPad|iPod/i.test(navigator.platform || navigator.userAgent);
  const shortcut = (apple:string, windows:string) => mac ? apple : windows;
  const orgId = useAuthStore(s => s.currentOrgId); const userId = useAuthStore(s => s.user?.id);
  const identity = userId + ':' + orgId; const identityRef = useRef(identity); identityRef.current = identity;
  const nodeToolsMeasure=useMeasuredSurface(),titleToolsMeasure=useMeasuredSurface();
  const [scene, setScene] = useState<KnowledgeScene>(empty); const sceneRef = useRef(scene); sceneRef.current = scene;
  const browseEpoch = useRef(0), suppressReveal = useRef(false);
  const [loadedIdentity, setLoadedIdentity] = useState<string | null>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | 'unavailable' | 'error'>('loading');
  const [history, setHistory] = useState<CanvasHistory>(noHistory);
  const [historyUnavailable, setHistoryUnavailable] = useState(false);
  const [error, setError] = useState(''); const [pending, setPending] = useState(false); const busy = useRef(false);const writeSession=useRef(0);
  const invalidateWriteSession=useCallback(()=>{writeSession.current++;},[]);
  const unconfirmed = useRef<{ operation: string; payload: Record<string, unknown>; id: string; key: string } | null>(null);
  const [tab, setTab] = useState<KnowledgeScope>('personal'); const [project, setProject] = useState('');
  const [multi, setMulti] = useState<Set<string>>(new Set());
  const [clipboard, setClipboard] = useState<BranchClipboard | null>(null);
  const [copyPending, setCopyPending] = useState(false);
  const copyGeneration = useRef(0);
  useLayoutEffect(() => { copyGeneration.current++; }, [identity]);
  const snapshotReady = useRef(new Map<string, Promise<boolean>>());
  const [historyNotice, setHistoryNotice] = useState('');
  const [copyNotice, setCopyNotice] = useState(false);
  useEffect(() => { setCopyNotice(!!clipboard && clipboard.mode === 'copy' && !copyPending); const timer = setTimeout(() => setCopyNotice(false), 2600); return () => clearTimeout(timer); }, [clipboard, copyPending]);
  useEffect(() => { if (!historyNotice) return; const timer = setTimeout(() => setHistoryNotice(''), 2600); return () => clearTimeout(timer); }, [historyNotice]);
  useEffect(() => setHistoryNotice(''), [identity]);
  const cancelledCuts = useRef(new Set<string>());
  const liveCuts = useRef(new Set<string>());
  const [marquee, setMarquee] = useState<{ start: Point; end: Point } | null>(null);
  const marqueeBase = useRef<Set<string>>(new Set());
  const [groupSelection,setGroupSelection]=useState<GroupSelection|null>(null);
  const [selectedRelation, setSelectedRelation] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null); const [query, setQuery] = useState('');
  const [navigationTick, setNavigationTick] = useState(0);
  const [historyFocus, setHistoryFocus] = useState<string[]>([]);
  const reducedMotion = useCanvasReducedMotion();
  const { view: pan, ref: viewRef, immediate: updateView, animate: animateView, cancel: cancelView } = useCanvasViewMotion(identity, reducedMotion);
  const nodeMotion = useCanvasNodeMotion(identity, reducedMotion);
  const marqueeRef = useRef(marquee); marqueeRef.current = marquee;
  const [previous, setPrevious] = useState<typeof pan | null>(null);
  const [folded, setFolded] = useState<Set<string>>(new Set()); const foldsRef = useRef(folded); foldsRef.current = folded; const [menu, setMenu] = useState<Menu | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null); const [editing, setEditing] = useState<KnowledgeObject | null>(null);
  const [filePreview,setFilePreview]=useState<{id:string;version:KnowledgeFileVersion;identity:string}|null>(null);
  const [imageView,setImageView]=useState<{id:string;src:string;key:string;title:string;identity:string}|null>(null);
  const [bodyHistory,setBodyHistory]=useState<string|null>(null);
  const [fileEntry,setFileEntry]=useState<{action:FileEntryAction;position:{left:number;top:number}}|null>(null);const fileOpenGeneration=useRef(0);
  const [fileObject, setFileObject] = useState<KnowledgeObject | null>(null); const [filePending, setFilePending] = useState(false);
  const [quick, setQuick] = useState<QuickDraft | null>(null);
  const editingBase = useRef('');
  const editors = useRef({ quick, draft, editing }); editors.current = { quick, draft, editing };
  type SavedDraft = {relationText?:RelationLabelDraft|null;visual?:typeof visual;annotation?:typeof annotation;mediaEditor?:typeof mediaEditor; mapEditor?:MapThemeDraft|null; quick: QuickDraft | null; draft: Draft | null; editing: KnowledgeObject | null; base: string; attempt: typeof unconfirmed.current };
  const [mediaEditor,setMediaEditor]=useState<{node:KnowledgeNode;mode:'video'|'link';draft?:MediaDraft;containerRevision?:number}|null>(null);
  const [relationText,setRelationText]=useState<RelationLabelDraft|null>(null);
  const [mediaView,setMediaView]=useState<{node:KnowledgeNode;source:'video'|'link'}|null>(null);
  const [mapEditor,setMapEditor]=useState<MapThemeDraft|null>(null);
  const [visual, setVisual] = useState<{node: KnowledgeNode; mode: VisualMode;boundaryKey?:string;draft?:VisualDraft;containerRevision?:number;targets?:KnowledgeNode[]} | null>(null);
  const [brush,setBrush]=useState(false);
  const [styleClipboard, setStyleClipboard] = useState<StyleSnapshot | undefined>();
  const [annotation, setAnnotation] = useState<{ node: KnowledgeNode; mode: AnnotationMode;draft?:AnnotationDraft;containerRevision?:number } | null>(null);
  const dirty = !!relationText || !!mapEditor || !!mediaEditor || !!visual || !!annotation || !!unconfirmed.current || !!(quick && (!quick.node || (quick.title !== quick.node.title || JSON.stringify(quick.titleFormat)!==JSON.stringify(quick.node.canvas_data?.title_format)))) || !!(draft && (draft.title || draft.body)) || !!(editing?.can_write && editing.body !== editingBase.current);
  const savedDraft: SavedDraft | null = dirty ? {relationText,visual,annotation,mediaEditor, mapEditor,quick, draft, editing, base: editingBase.current, attempt: unconfirmed.current } : null;
  const draftStore = useCanvasDraft(identity, savedDraft, status === 'ready' && loadedIdentity === identity);
  const closeDraft = (close: () => void) => { if (unconfirmed.current) { setError('保存结果尚未确认，请先点击刷新核对。'); return; } if (!dirty || window.confirm('放弃尚未保存的修改？')) { if (draftStore.clear()) close(); else setError('草稿未能安全清理，请保留页面并重试。'); } };
  const restoreDraft = async () => {
    const record = draftStore.recovery; if (!record || busy.current) return;
    const key = identity;
    try {
      const checkedScene = await knowledgeApi.scene();
      if (identityRef.current !== key) return;
      const cid = record.quick?.container ?? record.draft?.created?.container_id ?? record.draft?.container ?? record.editing?.container_id ?? record.mapEditor?.container ?? record.visual?.node.container_id ?? record.annotation?.node.container_id ?? record.mediaEditor?.node.container_id ?? record.relationText?.relation.container_id;
      const scope = checkedScene.containers.find(c => c.id === cid && c.role !== 'viewer');
      if (!scope && !(record.draft?.type === 'project' && !record.draft.created) && !(record.attempt && !record.quick && !record.draft && !record.editing)) throw new Error('草稿目标已移除或无编辑权限，无法恢复。');
      let recoveredBody: KnowledgeObject | null = null;
      let versionChanged = false;
      if(record.quick?.wrap&&!checkedScene.nodes.some(n=>n.id===record.quick!.wrap!.id&&n.container_id===record.quick!.container))throw new Error('原分支已移除，无法恢复父主题草稿');
      if (record.quick?.node && !checkedScene.nodes.some(n => n.id === record.quick!.node!.id)) throw new Error('草稿节点已移除，无法恢复');
      if (record.quick?.parent && !checkedScene.nodes.some(n => n.id === record.quick!.parent)) throw new Error('草稿父节点已移除，无法恢复');
      if (record.draft?.parent && !checkedScene.nodes.some(n => n.id === record.draft!.parent && n.container_id === record.draft!.container)) throw new Error('草稿父节点已移除，无法恢复');
      if(record.mapEditor){const c=checkedScene.containers.find(c=>c.id===record.mapEditor!.container);if(!c||c.role==='viewer')throw new Error('已无权限恢复全图样式草稿');versionChanged=versionChanged||c.revision!==record.mapEditor.revision;}
      if(record.quick?.wrap)versionChanged=versionChanged||checkedScene.nodes.find(n=>n.id===record.quick!.wrap!.id)?.revision!==record.quick.wrap.revision;
      if (record.quick?.node) versionChanged = versionChanged||checkedScene.nodes.find(n => n.id === record.quick!.node!.id)?.revision !== record.quick.node.revision;
      if (record.draft?.created) {
        const current = checkedScene.nodes.find(n => n.id === record.draft!.created!.id);
        if (!current) throw new Error('草稿对应的已创建节点已移除，无法恢复');
        versionChanged = versionChanged||current.revision !== record.draft.created.revision;
      }
      if(record.relationText){const r=checkedScene.relations.find(r=>r.id===record.relationText!.relation.id&&r.container_id===record.relationText!.relation.container_id);if(!r||!checkedScene.containers.some(c=>c.id===r.container_id&&c.role!=='viewer'))throw new Error('关联线已移除或无编辑权限，无法恢复草稿');versionChanged=versionChanged||JSON.stringify(r.appearance)!==JSON.stringify(record.relationText.relation.appearance)||checkedScene.containers.find(c=>c.id===r.container_id)?.revision!==record.relationText.containerRevision;}
      for(const dialog of [record.visual,record.annotation,record.mediaEditor])if(dialog){
        versionChanged=versionChanged||dialog.containerRevision!==undefined&&checkedScene.containers.find(c=>c.id===dialog.node.container_id)?.revision!==dialog.containerRevision;
        for(const node of [dialog.node,...('targets' in dialog?dialog.targets??[]:[])]){
          const current=checkedScene.nodes.find(n=>n.id===node.id&&n.container_id===node.container_id);
          if(!current||!checkedScene.containers.some(c=>c.id===node.container_id&&c.role!=='viewer'))throw new Error('弹窗目标已移除或无编辑权限，无法恢复草稿');
          versionChanged=versionChanged||current.revision!==node.revision||current.layout_revision!==node.layout_revision;
        }
        if('boundaryKey' in dialog&&dialog.boundaryKey&&!boundaryEntries(checkedScene.nodes.find(n=>n.id===dialog.node.id)?.canvas_data).some(b=>b.key===dialog.boundaryKey))throw new Error('原外框已移除，草稿仍保留，请核对最新内容');
      }
      if (record.editing) {
        const latest = await knowledgeApi.object(record.editing.id);
        if (identityRef.current !== key) return;
        if (!latest.can_write) throw new Error('已无编辑权限，无法恢复草稿');
        recoveredBody = record.editing; versionChanged = versionChanged||latest.revision !== record.editing.revision;
      }
      if (identityRef.current !== key) return;
      sceneRef.current = checkedScene; setScene(checkedScene);
      if (!draftStore.claim() || !draftStore.accept()) throw new Error('草稿已由其他标签页接管、更新或未能安全保存，请稍后重试。');
      if(record.relationText){setRelationText(record.relationText);setSelectedRelation(record.relationText.relation.id);}
      if(record.visual){setVisual(record.visual);setMulti(new Set((record.visual.targets??[record.visual.node]).map(n=>n.id)));}
      if(record.annotation)setAnnotation(record.annotation);
      if(record.mediaEditor)setMediaEditor(record.mediaEditor);
      if(record.mapEditor)setMapEditor({...record.mapEditor,theme:normalizeMapTheme(record.mapEditor.theme)});
      if (recoveredBody) { editingBase.current = record.base; setEditing(recoveredBody); }
      if (versionChanged) setError('内容已有新版本，草稿已恢复；保存仍会进行版本校验，请先对照最新内容。');
      cancelQuick.current = false; setQuick(record.quick); setDraft(record.draft); unconfirmed.current = record.attempt;
      if (record.quick) revealQuick(record.quick.position);
      if (scope) { setTab(scope.kind); setProject(scope.id); }
      if (record.attempt) setError('上次保存结果尚未确认，请点击刷新，使用原操作编号核对结果。');
    } catch (e) { if (identityRef.current === key) setError(message(e)); }
  };
  const quickInput = useRef<TitleEditorHandle | null>(null);
  const clickCaret=useRef<number|undefined>(undefined);
  const typingCaret=useRef(false),imeActive=useRef(false),imeEndAt=useRef(-Infinity),imeCanvasNode=useRef<string|null>(null);
  const mountQuickInput = useCallback((input: TitleEditorHandle | null) => { quickInput.current = input; input?.focus({ preventScroll: true }); if(typingCaret.current){input?.setSelectionRange(input.value.length,input.value.length);typingCaret.current=false;}else if(input){const range=editSelection(input.value,false);const offset=clickCaret.current;input.setSelectionRange(offset??range.start,offset??range.end);clickCaret.current=undefined;} }, []);
  const quickSaving = useRef(false); const cancelQuick = useRef(false);
  const createdNode = useRef<KnowledgeNode | null>(null);

  const [memberPanel, setMemberPanel] = useState<{ id: string; title: string; rows: KnowledgeMember[] } | null>(null);
  const [branchPreview, setBranchPreview] = useState<BranchPlan | null>(null);
  const viewport = useRef<HTMLDivElement>(null); const drag = useRef<Drag | null>(null); const space = useRef(false);
  useEffect(() => { setMulti(new Set()); setClipboard(null); setCopyPending(false); snapshotReady.current.clear(); liveCuts.current.clear(); cancelledCuts.current.clear(); setMarquee(null); }, [identity]);

  const container = (id: string) => scene.containers.find(c => c.id === id);
  const writable = (n: KnowledgeNode) => { const role = container(n.container_id)?.role; return role === 'editor' || role === 'manager'; };
  const scopeContainers = scene.containers.filter(c => c.kind === tab);
  const active = tab === 'project' ? (scopeContainers.find(c => c.id === project) ?? scopeContainers[0]) : scopeContainers[0];
  const [widthPreview,setWidthPreview]=useState<({id:string;x:number;y:number}&TopicResize)|null>(null);
  const nodes = scene.nodes.filter(n => n.container_id === active?.id).map(n=>widthPreview?.id===n.id?{...n,x:widthPreview.x,y:widthPreview.y,canvas_data:{...n.canvas_data,width:widthPreview.width,height:widthPreview.height}}:n); const byId = new Map(nodes.map(n => [n.id, n]));
  useEffect(() => { const belongs = (id: string | null) => scene.nodes.some(n => n.id === id && n.container_id === active?.id); setSelected(id => belongs(id) ? id : null); setMulti(old => new Set([...old].filter(belongs))); setMenu(null); }, [active?.id, scene.nodes]);
  const connection = useCanvasConnection({ viewport, view: pan, setView: updateView, nodes, relations: scene.relations, identity, onTap: (id,port) => { const n=nodes.find(n=>n.id===id);if(port && n) startQuick(n,port==='child');else if(nodes.some(n=>n.parent_id===id)) toggleFold(id); }, enabled: status === 'ready' && !pending && !unconfirmed.current && !quick && !draft && !editing && !memberPanel && !fileObject && !branchPreview });
  const presence = useCanvasPresence(identity + ':' + active?.id, reducedMotion);
  const motionContext = useRef({ active, scene, folded, presence, nodeMotion }); motionContext.current = { active, scene, folded, presence, nodeMotion };
  const refresh = useCallback(async (motion?: string) => {
    const key = identity,session=writeSession.current; const [next, record] = await Promise.all([knowledgeApi.scene(), knowledgeApi.history().catch(() => null)]);
    if (identityRef.current === key&&writeSession.current===session) {
      if (motion) {
        const ctx = motionContext.current, scope = ctx.active?.id;
        const before = canvasSnapshot(ctx.nodeMotion.project(ctx.scene.nodes), ctx.scene.relations, scope, ctx.folded, viewport.current);
        const nextFolds = new Set(ctx.folded);
        if (motion.endsWith('_canvas') && !suppressReveal.current) {
          for (const n of historyTargets(ctx.scene, next, scope)) {
            let p = summaryParent(next.nodes,n); const seen = new Set<string>();
            while (p && !seen.has(p)) { seen.add(p); nextFolds.delete(p); const parent=next.nodes.find(parent=>parent.id===p);p=parent?summaryParent(next.nodes,parent):null; }
          }
          setFolded(nextFolds);
        }
        const after = canvasSnapshot(next.nodes, next.relations, scope, nextFolds);
        if (next.containers.some(c => c.id === scope && c.role === ctx.active?.role)) {
          ctx.presence.transition(before, after, motion.endsWith('_canvas'), motion === 'delete_branches' || motion.endsWith('_canvas'));
          if (motion.endsWith('_canvas') || createsNode(motion) || motion === 'copy_branches' || motion === 'move_branches' || motion === 'update_title' || motion === 'update_canvas_data' || motion === 'delete_topic' || motion === 'update_map_theme') ctx.nodeMotion.animate(before.nodes, after.nodes);
        } else ctx.presence.cancel();
      } else motionContext.current.presence.cancel();
      sceneRef.current = next; setScene(next); setHistory(record ?? noHistory); setHistoryUnavailable(!record); setMulti(old => new Set([...old].filter(id => next.nodes.some(n => n.id === id)))); setSelected(id => id && next.nodes.some(n => n.id === id) ? id : null); }
    return next;
  }, [identity]);
  useEffect(() => {
    const controller = new AbortController();invalidateWriteSession();busy.current=false;quickSaving.current=false;setPending(false); const current = () => !controller.signal.aborted && identityRef.current === identity;
    suppressReveal.current = false; browseEpoch.current++;
    setAnnotation(null); setGroupSelection(null); setSelectedRelation(null); setHistory(noHistory); setHistoryUnavailable(false); setBranchPreview(null); setFolded(new Set()); setQuick(null); setFileObject(null);setFileEntry(null); setFilePending(false); setLoadedIdentity(identity); unconfirmed.current = null; setScene(empty); setStatus('loading'); setDraft(null); setEditing(null);setBodyHistory(null);setImageView(null);setFilePreview(null); setMemberPanel(null); setMenu(null); setSelected(null); setQuery(''); setProject(''); setError('');
    void (async () => {
      try {
        const availability = await knowledgeApi.available(controller.signal);
        if (!current()) return;
        if (!availability.enabled) { setStatus('unavailable'); return; }
        await knowledgeApi.initialize(); if (!current()) return;
        const [next, record] = await Promise.all([knowledgeApi.scene(controller.signal), knowledgeApi.history(controller.signal).catch(() => null)]);
        if (current()) { const first = next.containers.find(c => next.nodes.some(n => n.container_id === c.id)) ?? next.containers[0]; if (first) { setTab(first.kind); setProject(first.id); } setScene(next); setHistory(record ?? noHistory); setHistoryUnavailable(!record); setStatus('ready'); }
      } catch (e) { if (current()) { setError(message(e)); setStatus('error'); } }
    })();
    return () => {controller.abort();invalidateWriteSession();};
  }, [identity,invalidateWriteSession]);
  const act = async (operation: string, payload: Record<string, unknown>, onFailure?: () => void) => {
    if (busy.current || status !== 'ready') { onFailure?.(); return false; }
    if (operation === 'update_title' && !unconfirmed.current && !payload.nodes) {
      const target = sceneRef.current.nodes.find(n => n.id === payload.object_id);
      if (target && typeof payload.title === 'string') {
        const current = sceneRef.current.nodes.filter(n => n.container_id === target.container_id);
        const sizes = contentSizes(current), changedSizes = contentSizes(current, { id: target.id, title: payload.title, format:payload.title_format as TitleFormat|undefined });
        const owner=structureRoot(current,target.id,container(target.container_id)?.canvas_theme?.layout);
        const layout = owner?arrangeStructure(current.map(n=>n.id===target.id?{...n,title:payload.title as string,canvas_data:{...n.canvas_data,title_format:payload.title_format as TitleFormat|undefined}}:n),owner.id,changedSizes,container(target.container_id)?.canvas_theme?.layout,container(target.container_id)?.canvas_theme):changedSizes.get(target.id)!.height > sizes.get(target.id)!.height ? reflowContent(current, changedSizes) : current;
        const changed = layout.filter(n => { const old = current.find(o => o.id === n.id)!; return n.x !== old.x || n.y !== old.y; });
        if (changed.length) payload = { ...payload, expected_container_revision: sceneRef.current.containers.find(c => c.id === target.container_id)!.revision, nodes: changed.map(n => ({ node_id: n.node_id, expected_revision: n.layout_revision, position: { x: n.x, y: n.y } })) };
      }
    }
    const requestKey = JSON.stringify({ operation, payload });
    if (unconfirmed.current && unconfirmed.current.key !== requestKey) {
      setError('上次保存结果尚未确认，请先点击刷新核对，或保持原内容重试。'); onFailure?.(); return false;
    }
    const attempt = unconfirmed.current ?? { operation, payload, id: crypto.randomUUID(), key: requestKey };
    unconfirmed.current = attempt;
    busy.current = true; setPending(true); setError(''); setHistoryNotice(''); const key = identity, session=writeSession.current, browseAtStart = browseEpoch.current;
    try {
      const result = await knowledgeApi.command(operation, payload, attempt.id);
      if (identityRef.current !== key||writeSession.current!==session) return false;
      if (browseEpoch.current !== browseAtStart) suppressReveal.current = true;
      const next = await refresh(operation); if (identityRef.current !== key||writeSession.current!==session) return false;
      unconfirmed.current = null;
      if (result.data.object_id && createsNode(operation)) {
        if (browseEpoch.current === browseAtStart) setSelected(result.data.object_id);
        const object = next.nodes.find(n => n.id === result.data.object_id); createdNode.current = object ?? null;
        const owner = next.containers.find(c => c.id === object?.container_id);
        if (owner && browseEpoch.current === browseAtStart) { setTab(owner.kind); if (owner.kind === 'project') setProject(owner.id); }
      }
      return true;
    } catch (e) {
      if (identityRef.current === key&&writeSession.current===session) {
        onFailure?.();
        const rejected = typeof e === 'object' && e !== null && 'status' in e && typeof e.status === 'number' && e.status >= 400 && e.status < 500;
        if (rejected) unconfirmed.current = null;
        setError(message(e) + (rejected ? '' : '；保存结果尚未确认，请保持原内容重试或点击刷新核对。'));
        try { await refresh(); } catch { /* Keep the draft and show the original failure. */ }
      }
      return false;
    }
    finally { if(identityRef.current===key&&writeSession.current===session){busy.current = false; setPending(false);} }
  };
  // Reconcile the exact acknowledged payload, never the text typed after it was sent.
  const reconcileSaved = (operation: string, payload: Record<string, unknown>) => {
    const current = editors.current;
    if(operation==='create_relation_topic')payload={...payload,title:'自由主题'};
    if(operation==='create_template'||operation==='create_topics'||operation==='insert_parent')payload={...payload,title:(payload.topics as {title:string;title_format?:TitleFormat}[])?.[0]?.title,title_format:(payload.topics as {title_format?:TitleFormat}[])?.[0]?.title_format};
    const created = createdNode.current ? { ...createdNode.current, title: String(payload.title), revision: 1 } : null;
    if (current.quick) {
      const q = current.quick;
      const n = createsNode(operation) ? created : sceneRef.current.nodes.find(n => n.id === payload.object_id);
      if (q.title.trim() === payload.title && JSON.stringify(q.titleFormat?editRuns(q.title,q.title.trim(),q.titleFormat):undefined)===JSON.stringify(payload.title_format??undefined)) setQuick(null);
      else if (n) setQuick({ ...q, standalone:q.standalone||!q.node, wrap:undefined, node: createsNode(operation) ? n : { ...n, title: String(payload.title), revision: Number(payload.expected_revision) + 1 } });
    }
    if (current.draft && createsNode(operation)) {
      const d = current.draft;
      if (d.title.trim() === payload.title && (d.type === 'project' || d.body === payload.body)) setDraft(null);
      else if (created) setDraft({ ...d, created });
    }
    if (current.draft?.created && (operation === 'update_title' || operation === 'save_document')) {
      const d = current.draft;
      const latest = sceneRef.current.nodes.find(n => n.id === d.created!.id);
      const n = latest ? { ...latest, revision: Number(payload.expected_revision) + 1, title: operation === 'update_title' ? String(payload.title) : d.created!.title } : d.created;
      setDraft(operation === 'save_document' && d.body === payload.body && d.title.trim() === n?.title ? null : { ...d, created: n ?? d.created });
    }
    if (current.editing && operation === 'save_document') {
      editingBase.current = String(payload.body);
      if(current.editing.id===payload.object_id)setEditing({ ...current.editing, revision: Number(payload.expected_revision) + 1 });
    }
  };
  const checkSave = async () => {
    const attempt = unconfirmed.current;
    if (attempt) { if (await act(attempt.operation, attempt.payload)) { reconcileSaved(attempt.operation, attempt.payload); if((attempt.operation==='create_template'||attempt.operation==='create_relation_topic')&&createdNode.current)locate(createdNode.current.id); if(attempt.operation==='update_relation'&&relationText?.relation.id===attempt.payload.relation_id)setRelationText(null); if (attempt.operation === 'update_map_theme')setMapEditor(null); if (attempt.operation === 'update_canvas_data') { setAnnotation(null); setVisual(null);setMediaEditor(null); } } }
    else { setError(''); try { await refresh(); } catch (e) { setError(message(e)); } }
  };
  const saveBody = async () => {
    if (!editing||!editing.can_write||busy.current) return;
    if(!unconfirmed.current&&editing.body===editingBase.current)return;
    const payload = { object_id: editing.id, expected_revision: editing.revision, body: editing.body };
    if (unconfirmed.current) { await checkSave(); return; }
    if (await act('save_document', payload)) reconcileSaved('save_document', payload);
  };
  const saveDraft = async () => {
    if (!draft || !draft.title.trim()) return;
    if (unconfirmed.current) { await checkSave(); return; }
    if (draft.created) {
      let n = draft.created;
      if (draft.title.trim() !== n.title) {
        const payload = { object_id: n.id, expected_revision: n.revision, title: draft.title.trim() };
        if (!await act('update_title', payload)) return;
        n = { ...n, title: draft.title.trim(), revision: n.revision + 1 };
        const acknowledged = n;
        setDraft(d => d ? { ...d, created: acknowledged } : d);
        if (draft.type === 'project') { setDraft(d => d?.title === draft.title ? null : d); return; }
      }
      const payload = { object_id: n.id, expected_revision: n.revision, body: draft.body };
      if (await act('save_document', payload)) setDraft(d => d && d.title === draft.title && d.body === draft.body ? null : d ? { ...d, created: { ...n, revision: n.revision + 1 } } : d);
      return;
    }
    const payload = draft.type === 'project' ? { title: draft.title.trim(), position: draft.position } : { title: draft.title.trim(), position: draft.position, container_id: draft.container, parent_id: draft.parent, kind: draft.type, body: draft.body };
    const operation = draft.type === 'project' ? 'create_project' : 'create_object';
    if (await act(operation, payload)) reconcileSaved(operation, payload);
  };
  const sourceLocated = useRef('');
  const [sourceClosed,setSourceClosed] = useState(false);
  const sourceQuery = new URLSearchParams(window.location.search);
  const sourceId = sourceQuery.get('object'); const sourceRevision = Number(sourceQuery.get('revision'));
  useEffect(() => {
    if (status !== 'ready' || loadedIdentity !== identity) return;
    const key = identity + ':' + window.location.search;
    if (sourceLocated.current === key) return;
    sourceLocated.current = key;
    const q = new URLSearchParams(window.location.search); const id=q.get('object');
    const node=scene.nodes.find(n => n.id===id);
    const scope=scene.containers.find(c => c.id===(node?.container_id ?? q.get('container')));
    if (id && !node) { if (!(Number(q.get('revision')) > 0)) setError('来源已移除或无权访问'); return; }
    if (scope) {setTab(scope.kind);if (scope.kind==='project')setProject(scope.id);}
    if (node) {
      const rect=viewport.current?.getBoundingClientRect();
      if (rect)updateView({x:rect.width/2-(node.x+120),y:rect.height/2-(node.y+65),z:1});
      setSelected(node.id);setFolded(new Set());
    }
  }, [status,loadedIdentity,identity,scene,updateView]);
  const locate = (id: string, source: KnowledgeNode[] = sceneRef.current.nodes) => {
    browseEpoch.current++; suppressReveal.current = false;
    const index = new Map(source.map(n => [n.id, n]));
    const n = index.get(id), r = viewport.current?.getBoundingClientRect(); if (!n || !r) return;
    const owner = container(n.container_id); if (owner) { setTab(owner.kind); setProject(owner.id); }
    setMulti(new Set()); setSelectedRelation(null); setMenu(null); focusCanvas();
    setPrevious(viewRef.current); animateView(()=>{const safe=canvasSafeArea(viewport.current!),el=viewport.current?.querySelector<HTMLElement>(`[data-node-id="${n.id}"]`);return {x:safe.x+safe.width/2-(n.x+(el?.offsetWidth||240)/2),y:safe.y+safe.height/2-(n.y+(el?.offsetHeight||130)/2),z:1};}); setSelected(id);
    setFolded(old => { const next = new Set(old); let p = summaryParent(source,n); const seen = new Set<string>(); while (p && !seen.has(p)) { seen.add(p); next.delete(p); const parent=index.get(p);p=parent?summaryParent(source,parent):null; } return next; });
  };
  const hidden = (n: KnowledgeNode) => nodeHidden(nodes,folded,n);
  const navigationId = selected ?? [...multi].filter(id => byId.has(id)).at(-1) ?? null;
  const drawnScene = () => nodeMotion.project(nodes);
  const toggleFold = (id: string, expand?: boolean) => {
    browseEpoch.current++; suppressReveal.current = false;
    const open = expand ?? folded.has(id);
    const next = new Set(folded); if (open) next.delete(id); else next.add(id);
    presence.transition(canvasSnapshot(drawnScene(), scene.relations, active?.id, folded, viewport.current), canvasSnapshot(nodes, scene.relations, active?.id, next));
    foldsRef.current=next;setFolded(next);
    if (!open) {
      const descendants = new Set([id]); let changed = true;
      while (changed) { changed = false; for (const n of nodes) if (n.parent_id && descendants.has(n.parent_id) && !descendants.has(n.id)) { descendants.add(n.id); changed = true; } }
      if ((selected && descendants.has(selected)) || [...multi].some(n => descendants.has(n))) { setSelected(id); setMulti(new Set([...multi].filter(n => !descendants.has(n)))); }
    }
    focusCanvas();
  };
  const foldAll=(expand:boolean,ids?:string[])=>{
    if(browseBlocked||drag.current||marqueeRef.current)return;
    const targets=ids??(chosen.size?[...chosen]:nodes.filter(n=>!n.parent_id).map(n=>n.id));
    const next=branchFoldState(nodes,foldsRef.current,targets,expand);
    browseEpoch.current++;suppressReveal.current=false;setMenu(null);setSelectedRelation(null);
    presence.transition(canvasSnapshot(drawnScene(),scene.relations,active?.id,foldsRef.current,viewport.current),canvasSnapshot(nodes,scene.relations,active?.id,next));
    foldsRef.current=next;setFolded(next);
    const survivors=[...new Set([...chosen].map(id=>visibleSelection(nodes,next,id)).filter((id):id is string=>!!id))];
    if(survivors.length){const anchor=navigationId?visibleSelection(nodes,next,navigationId):null;setSelected(anchor??survivors[0]);setMulti(new Set(survivors.filter(id=>id!==(anchor??survivors[0]))));}
    focusCanvas();
  };
  const goToCenter=()=>{
    if(browseBlocked||drag.current||marqueeRef.current)return;
    const root=centerTopic(nodes,navigationId,active?.id),area=viewport.current?.getBoundingClientRect();if(!root||!area)return;
    browseEpoch.current++;suppressReveal.current=true;setMenu(null);setSelectedRelation(null);setMulti(new Set());setSelected(root.id);
    const painted=nodeMotion.project(nodes).find(n=>n.id===root.id)??root,element=viewport.current?.querySelector<HTMLElement>(`[data-node-id="${root.id}"]`);
    setPrevious(viewRef.current);animateView(current=>{const safe=canvasSafeArea(viewport.current!),next=centerTopicView(current,painted,{width:element?.offsetWidth||topicWidth(root),height:element?.offsetHeight||170},safe);return {...next,x:next.x+safe.x,y:next.y+safe.y};});focusCanvas();
  };
  const occlusionStamp=useCanvasOcclusion(viewport,identity,status==='ready'&&loadedIdentity===identity);
  useCanvasEditingVisibility(viewport,!!quick,identity,viewRef,updateView,imeActive,JSON.stringify([quick?.title,quick?.titleFormat,quick?.node?.id,occlusionStamp,titleToolsMeasure.size]));
  useEffect(() => {
    if (suppressReveal.current) return;
    if (!navigationId || quick || branchPreview || connection.preview || drag.current || marqueeRef.current || nodeMotion.active || status !== 'ready') return;
    const node = scene.nodes.find(n => n.id === navigationId), area = viewport.current;
    if (!node || !area) return;
    const element = area.querySelector<HTMLElement>(`[data-node-id="${node.id}"]`);
    if (!element) return;
    const box = area.getBoundingClientRect(); if (box.width < 100 || box.height < 150) return;
    animateView(view => { const safe=canvasSafeArea(area),shifted={...view,x:view.x-safe.x,y:view.y-safe.y},revealed=revealNode(shifted, node, { width: element.offsetWidth || 272, height: element.offsetHeight || 170 }, safe),next={...revealed,x:revealed.x+safe.x,y:revealed.y+safe.y}; return next.x === view.x && next.y === view.y ? view : next; });
  }, [navigationId, navigationTick, multi, folded, scene, status, quick, branchPreview, connection.preview, nodeMotion.active, animateView,occlusionStamp]);
  useLayoutEffect(() => {
    if (!historyFocus.length) return;
    const area = viewport.current, box = area?.getBoundingClientRect();
    if (area && box) {
      const cards = historyFocus.flatMap(id => { const n = scene.nodes.find(n => n.id === id), el = area.querySelector<HTMLElement>(`[data-node-id="${id}"]`); return n ? [{ ...n, width: el?.offsetWidth || 272, height: el?.offsetHeight || 170 }] : []; });
      const safe=canvasSafeArea(area!);const fit = fitSafeView(cards, safe);
      if (fit) animateView(current => { const z = Math.min(fit.z, current.z); return { z, x: safe.x+safe.width / 2 - (safe.x+safe.width / 2 - fit.x) / fit.z * z, y: safe.y+safe.height / 2 - (safe.y+safe.height / 2 - fit.y) / fit.z * z }; });
    }
    setHistoryFocus([]);
  }, [historyFocus, scene, animateView]);
  const point = (x: number, y: number) => { const r = viewport.current!.getBoundingClientRect(); const view = viewRef.current; return { x: (x - r.left - view.x) / view.z, y: (y - r.top - view.y) / view.z }; };
  const openCreate = (position: Point, parent: string | null, type?: Draft['type'], inheritContainer?: string) => {
    const n = parent ? byId.get(parent) : undefined; const c = n ? container(n.container_id) : inheritContainer ? container(inheritContainer) : active;
    setError(''); setMenu(null); setDraft({ type: type ?? (tab === 'project' && !c ? 'project' : 'topic'), container: c?.id ?? '', parent, title: '', body: '', position: freePosition(position) });
  };
  const openFiles=async(n:KnowledgeNode)=>{if(busy.current||filePending||editors.current.editing&&editors.current.editing.body!==editingBase.current)return;const key=identity,generation=++fileOpenGeneration.current;try{const object=await knowledgeApi.object(n.id);if(identityRef.current===key&&generation===fileOpenGeneration.current){setFileEntry(null);setEditing(null);setSelected(n.id);setMulti(new Set());setFileObject(object);}}catch(e){if(identityRef.current===key&&generation===fileOpenGeneration.current)setError(message(e));}};
  const openFileAction=async(n:KnowledgeNode,action:FileEntryAction)=>{
    if(busy.current||filePending||editors.current.editing&&editors.current.editing.body!==editingBase.current)return;
    const generation=++fileOpenGeneration.current,key=identity;
    try{const object=await knowledgeApi.object(n.id);if(identityRef.current!==key||generation!==fileOpenGeneration.current)return;if(!object.can_write){setError('当前节点已无编辑权限');return;}
      const rect=viewport.current?.querySelector<HTMLElement>(`[data-node-id="${n.id}"] [aria-label="文件资料"]`)?.getBoundingClientRect();
      setEditing(null);setSelected(n.id);setMulti(new Set());setFileEntry({action,position:{left:Math.max(8,Math.min(rect?.left??100,window.innerWidth-410)),top:Math.max(70,(rect?.bottom??150)+8)}});setFileObject(object);
    }catch(e){if(identityRef.current===key)setError(message(e));}
  };
  const openEditor = async (n: KnowledgeNode) => {
    if (busy.current || filePending) return;if(editors.current.editing?.id===n.id)return;if(editors.current.editing&&editors.current.editing.body!==editingBase.current)return; const key = identity; setError('');
    try { const object = await knowledgeApi.object(n.id); if (identityRef.current === key) { setFileObject(null); editingBase.current = object.body; setEditing(object); } }
    catch (e) { if (identityRef.current === key) setError(message(e)); }
  };
  const focusFrame = useRef<number | null>(null);
  const focusContext = useRef({ identity, blocked: false });
  focusContext.current = { identity, blocked: status !== 'ready' || loadedIdentity !== identity || !!mapEditor || !!quick || !!draft || !!editing || !!memberPanel || !!fileObject || !!menu || !!draftStore.recovery };
  const focusCanvas = useCallback(() => {
    const key = identityRef.current;
    if (focusFrame.current !== null) cancelAnimationFrame(focusFrame.current);
    focusFrame.current = requestAnimationFrame(() => { focusFrame.current = null; const input = document.activeElement?.matches('input,textarea,select,[contenteditable="true"]'); if (!input && focusContext.current.identity === key && !focusContext.current.blocked) viewport.current?.focus({ preventScroll: true }); });
  }, []);
  useEffect(() => () => { if (focusFrame.current !== null) cancelAnimationFrame(focusFrame.current); }, [identity]);
  const returnFromDialog = (event: Event) => { event.preventDefault(); focusCanvas(); };

  const contentSizes = (items: KnowledgeNode[], rename?: { id: string; title: string; format?:TitleFormat }) => {if(rename)items=items.map(n=>n.id===rename.id?{...n,title:rename.title,canvas_data:{...n.canvas_data,title_format:rename.format}}:n);return new Map(items.map(n => {
    const el = viewport.current?.querySelector<HTMLElement>(`[data-node-id="${n.id}"]`);
    const baseFont=mapAppearance(n,items,active?.canvas_theme)?.style?.font_size??15;
    const font=richFont(rename?.id===n.id?rename.format:n.canvas_data?.title_format,baseFont);
    let height = el?.offsetHeight || topicSize({...n,canvas_data:mapAppearance(n,items,active?.canvas_theme)}).height;
    const title = rename?.id === n.id ? rename.title : n.title;
    const width=topicWidth({...n,title,canvas_data:{...mapAppearance(n,items,active?.canvas_theme),...(rename?.id===n.id?{title_format:rename.format}:{})}},baseFont);
    if ((rename?.id === n.id) || (el && (el.querySelector('h3')?.textContent !== title || el.offsetWidth!==width))) {
      const clone = el?.cloneNode(true) as HTMLElement | undefined;
      if (clone) { clone.removeAttribute('data-node-id'); clone.setAttribute('aria-hidden', 'true'); clone.inert = true; paintTitle(clone.querySelector('h3')!,title,rename?.id===n.id?rename.format:n.canvas_data?.title_format); Object.assign(clone.style, { width:width+'px',boxSizing:'border-box',visibility: 'hidden', pointerEvents: 'none', left: '0', top: '0', transform: 'none' }); viewport.current?.append(clone); height = clone.offsetHeight || titleHeight(title,width,font); clone.remove(); }
      else height = titleHeight(title,width,font);
    }
    return [n.id, { width, height }];
  }));};
  const newTopicFont=(parent:string|null,cid:string)=>{
    const sample:KnowledgeNode={id:'measure-theme',node_id:'measure-theme',container_id:cid,parent_id:parent,title:'',kind:'topic',revision:1,layout_revision:1,x:0,y:0};
    return mapAppearance(sample,[...sceneRef.current.nodes,sample],container(cid)?.canvas_theme)?.style?.font_size??15;
  };
  const freePosition = (wanted: Point, height = 170, containerId = active?.id) => {
    const occupied = sceneRef.current.nodes.filter(n => n.container_id === containerId).map(n => {
      const element = viewport.current?.querySelector<HTMLElement>(`[data-node-id="${n.id}"]`);
      return { x: n.x, y: n.y, width: element?.offsetWidth || 272, height: element?.offsetHeight || 170 };
    });
    let y = wanted.y;
    // Advance past every intersecting card, including long titles and hidden branches.
    for (let i = 0; i <= occupied.length; i++) {
      const hit = occupied.find(n => wanted.x < n.x + n.width + 24 && wanted.x + 272 + 24 > n.x && y < n.y + n.height + 24 && y + height + 24 > n.y);
      if (!hit) break;
      y = hit.y + hit.height + 24;
    }
    return { x: wanted.x, y };
  };
  const revealQuick = (position: Point, size = {width:320,height:230}) => {
    const rect = viewport.current?.getBoundingClientRect(); if (!rect) return;
    const view=viewRef.current,safe=canvasSafeArea(viewport.current!);
    updateView(revealScreenRect(view,{x:position.x*view.z+view.x,y:position.y*view.z+view.y,width:size.width*view.z,height:size.height*view.z},safe));
  };
  const startQuick = (n: KnowledgeNode, child: boolean, fromLine = false, before = false) => {
    if (busy.current || unconfirmed.current || copyPending || !writable(n)) return;
    if (drag.current?.type === 'node' && !drag.current.moved) { edgePan.stop(); drag.current = null; }
    const measuredNodes=sceneRef.current.nodes.filter(v=>v.container_id===n.container_id);
    let position = quickPlacement(measuredNodes,n,child,contentSizes(measuredNodes),undefined,before);
    const parent = child ? n.id : n.parent_id;
    const current=sceneRef.current.nodes.filter(v=>v.container_id===n.container_id),owner=parent?structureRoot(current,parent,active?.canvas_theme?.layout):undefined;
    if(owner){const virtual={...n,id:'new-draft',node_id:'new-draft',parent_id:parent,canvas_data:{},sort_order:child?Math.max(0,...current.filter(v=>v.parent_id===parent).map(v=>v.sort_order??v.y))+1024:(n.sort_order??n.y)+(before?-0.5:0.5),x:position.x,y:position.y};const arranged=arrangeStructure([...current,virtual],owner.id,contentSizes([...current,virtual]),active?.canvas_theme?.layout,active?.canvas_theme);const placed=arranged.find(v=>v.id===virtual.id)!;position={x:placed.x,y:placed.y};}
    setFolded(old => { const next = new Set(old); if (parent) next.delete(parent); return next; });
    setMenu(null); setError(''); cancelQuick.current = false;
    setQuick({ node: null, container: n.container_id, parent, position, title: '', fromLine, after: child || before ? undefined : n.id, before: before ? n.id : undefined }); revealQuick(position);
  };
  const createFromLine = (source: KnowledgeNode, position: Point, port?: 'child'|'sibling',manualLayout?:ManualLayout) => {
    setSelected(source.id); setMenu(null); setError(''); cancelQuick.current = false;
    setFolded(old => { const next = new Set(old); const parent=port==='sibling'?source.parent_id:source.id;if(parent)next.delete(parent);return next; });
    const placement = position;
    setQuick({ node: null, container: source.container_id, parent: port==='sibling'?source.parent_id:source.id, after:port==='sibling'?source.id:undefined, position: placement, title: '', fromLine: true, fixedPosition:true,lineSource:source.id,linePort:port,manualLayout });
    revealQuick(placement);
  };
  const rename = (n: KnowledgeNode,seed?:string,caret?:number) => {
    if (busy.current || unconfirmed.current || copyPending || !writable(n)) return;
    setMenu(null); setError(''); cancelQuick.current = false;
    clickCaret.current=editSelection(n.title,false).start===0?undefined:caret;typingCaret.current=seed!==undefined;setQuick({ node: n, container: n.container_id, parent: n.parent_id, position: { x: n.x, y: n.y }, title: seed??n.title, titleFormat:seed===undefined?n.canvas_data?.title_format:n.canvas_data?.title_format?{runs:[],align:n.canvas_data.title_format.align}:undefined });
    revealQuick({ x: n.x, y: n.y },contentSizes([n]).get(n.id));
  };
  const startParent=(n:KnowledgeNode)=>{
    if(historyBlocked||!writable(n)||toolNodes.length!==1)return;
    if(drag.current?.type==='node'&&!drag.current.moved){edgePan.stop();drag.current=null;}
    const plan=planParentTopic(sceneRef.current.nodes.filter(v=>v.container_id===n.container_id),n,'新主题',contentSizes(nodes),active?.canvas_theme?.font_size??15,active?.canvas_theme);
    setMenu(null);setError('');cancelQuick.current=false;setQuick({node:null,wrap:n,container:n.container_id,parent:n.parent_id,position:plan.position,title:''});revealQuick(plan.position);
  };
  const quickTitleStyle=(q:QuickDraft)=>{
    const heading=q.node?viewport.current?.querySelector<HTMLElement>(`[data-node-id="${q.node.id}"] h3`):null;
    if(heading)return displayedTitleStyle(heading);
    const sample:KnowledgeNode={id:'draft-style',node_id:'draft-style',container_id:q.container,parent_id:q.parent,kind:'topic',title:q.title,revision:1,layout_revision:1,x:0,y:0};
    const data=mapAppearance(sample,nodes,active?.canvas_theme);return {...displayedTitleStyle(viewport.current),fontSize:newTopicFont(q.parent,q.container),fontWeight:400,...titleStyle(data),...(data?.style?.color?{color:data.style.color}:{})};
  };
  const saveQuick = async (next?: 'sibling' | 'child') => {
    if (!quick || quickSaving.current || cancelQuick.current) return;
    if (unconfirmed.current) { await checkSave(); return; }
    const title = quick.title.trim() || (!quick.node ? '新主题' : '');
    const titleFormat=quick.titleFormat?editRuns(quick.title,title,quick.titleFormat):undefined;
    const presentation=titleFormat?{title_format:titleFormat}:{};
    if (!title) { setQuick(null); focusCanvas(); return; }
    quickSaving.current = true; const key = identity,session=writeSession.current;
    let ok = true; createdNode.current = null;
    if(quick.wrap){
      const current=sceneRef.current.nodes.filter(n=>n.container_id===quick.container),target=current.find(n=>n.id===quick.wrap!.id);
      if(!target){quickSaving.current=false;setError('原分支已移除，请刷新后重试');return;}
      const plan=planParentTopic(current,target,title,contentSizes(current),active?.canvas_theme?.font_size??15,active?.canvas_theme,titleFormat);
      ok=await act('insert_parent',{container_id:quick.container,expected_container_revision:sceneRef.current.containers.find(c=>c.id===quick.container)!.revision,object_id:quick.wrap.id,expected_revision:quick.wrap.revision,parent_id:quick.parent,topics:[{title,...presentation,parent_index:-1,position:plan.position,...(plan.structure?{structure:plan.structure}:{})}],nodes:plan.nodes});
    }else if (quick.node) {
      if (title !== quick.node.title || JSON.stringify(titleFormat)!==JSON.stringify(quick.node.canvas_data?.title_format)) ok = await act('update_title', { object_id: quick.node.id, expected_revision: quick.node.revision, title, ...presentation, ...(quick.fixedPosition&&quick.fromLine?{nodes:[],expected_container_revision:sceneRef.current.containers.find(c=>c.id===quick.container)!.revision}:{}), ...(titleFormat?{expected_container_revision:sceneRef.current.containers.find(c=>c.id===quick.container)!.revision}:{}) });
    } else {
      // Reserve space for a long title before its card is rendered. Retries retain the original payload.
      const position = quick.fromLine ? quick.position : freePosition(quick.position, titleHeight(title,272,richFont(titleFormat,newTopicFont(quick.parent,quick.container))), quick.container);
      let placement = position; let insertion: Record<string, unknown> = {};
      if (quick.fixedPosition && quick.after) {
        insertion={after_object_id:quick.after,expected_container_revision:sceneRef.current.containers.find(c=>c.id===quick.container)!.revision,nodes:[]};
      } else if (quick.after || quick.before) {
        const latestNodes = sceneRef.current.nodes.filter(n => n.container_id === quick.container);
        const target = latestNodes.find(n => n.id === (quick.after ?? quick.before));
        if (!target) { quickSaving.current = false; setError('同级插入目标已更新，请刷新后重试'); return; }
        const virtual = { ...target, id: 'new-draft', node_id: 'new-draft', title, x: position.x, y: position.y,canvas_data:titleFormat?{title_format:titleFormat}:{} };
        const sizes = contentSizes([...latestNodes,virtual]);
        const plan = planBranch([...latestNodes, virtual], virtual.id, { x: 0, y: 0 }, { x: target.x + 50, y: quick.before ? target.y + 1 : target.y + (sizes.get(target.id)?.height ?? 170) - 1 }, sizes, new Set([target.id]),active?.canvas_theme?.layout,active?.canvas_theme);
        placement = plan.nodes.find(n => n.id === virtual.id)!;
        insertion = { ...(quick.before ? { before_object_id: quick.before } : { after_object_id: quick.after }), expected_container_revision: sceneRef.current.containers.find(c => c.id === quick.container)!.revision, nodes: plan.changed.filter(n => n.id !== virtual.id).map(n => ({ node_id: n.node_id, expected_revision: n.layout_revision, position: { x: n.x, y: n.y } })) };
      }
      const current=sceneRef.current.nodes.filter(n=>n.container_id===quick.container);
      const owner=quick.parent?structureRoot(current,quick.parent,active?.canvas_theme?.layout):undefined;
      if(quick.structure || (owner&&!quick.fixedPosition)){
        const peers=current.filter(n=>n.parent_id===quick.parent).sort(siblingCompare),at=peers.findIndex(n=>n.id===(quick.before??quick.after));
        const order=at<0?Math.max(0,...peers.map(n=>n.sort_order??n.y))+1024:(peers[at].sort_order??peers[at].y)+(quick.before?-0.5:0.5);
        const virtual:KnowledgeNode={id:'new-draft',node_id:'new-draft',container_id:quick.container,parent_id:quick.parent,kind:'topic',title,revision:1,layout_revision:1,x:position.x,y:position.y,sort_order:order,canvas_data:{...(quick.structure?{structure:quick.structure}:{}),...(titleFormat?{title_format:titleFormat}:{})}};
        const sizes=contentSizes([...current,virtual]);
        const arranged=arrangeStructure([...current,virtual],owner?.id??virtual.id,sizes,active?.canvas_theme?.layout,active?.canvas_theme),draft=arranged.find(n=>n.id===virtual.id)!;
        ok=await act('create_template',{container_id:quick.container,expected_container_revision:sceneRef.current.containers.find(c=>c.id===quick.container)!.revision,parent_id:quick.parent,topics:[{title,...presentation,parent_index:-1,position:{x:draft.x,y:draft.y},...(quick.structure?{structure:quick.structure}:{})}],nodes:structureMoves(current,arranged),...(quick.after?{after_object_id:quick.after}:{}),...(quick.before?{before_object_id:quick.before}:{})});
      } else ok = await act('create_object', { container_id: quick.container, parent_id: quick.parent, kind: 'topic', title, ...presentation, ...(quick.fixedPosition?{manual_layout:quick.manualLayout??{},expected_container_revision:sceneRef.current.containers.find(c=>c.id===quick.container)!.revision}:{}), body: '', position: { x: placement.x, y: placement.y }, ...insertion });
    }
    if (identityRef.current !== key||writeSession.current!==session) return;
    quickSaving.current = false;
    if (!ok) { quickInput.current?.focus({ preventScroll: true }); return; }
    const latest = quick.node ? sceneRef.current.nodes.find(n => n.id === quick.node!.id) : createdNode.current;
    const node = latest ? { ...latest, title, canvas_data:{...latest.canvas_data,...presentation}, revision: quick.node ? quick.node.revision + (title !== quick.node.title || JSON.stringify(titleFormat)!==JSON.stringify(quick.node.canvas_data?.title_format) ? 1 : 0) : 1 } : null;
    if (editors.current.quick?.title !== quick.title || JSON.stringify(editors.current.quick?.titleFormat)!==JSON.stringify(quick.titleFormat)) { if (node) setQuick(q => q ? { ...q, standalone:q.standalone||!q.node, wrap:undefined, node } : q); return; }
    setQuick(null); if (node) setSelected(node.id);
    if (next && node) startQuick(node, next === 'child', !!quick.fromLine && next === 'child'); else focusCanvas();
  };
  const historyBlocked = !!filePreview || !!imageView || !!relationText || !!mapEditor || !!mediaEditor || !!mediaView || filePending || !!visual || !!annotation || copyPending || pending || status !== 'ready' || !!quick || !!draft || !!editing || !!memberPanel || !!fileObject || !!branchPreview || !!connection.preview || !!unconfirmed.current;
  const browseBlocked = !!filePreview || !!imageView || !!relationText || !!mapEditor || !!mediaEditor || !!mediaView || !!visual || !!annotation || status !== 'ready' || !!quick || !!draft || !!editing || !!memberPanel || !!fileObject || !!branchPreview || !!connection.preview || !!draftStore.recovery;
  const historyReason = (direction: 'undo' | 'redo') => pending ? '正在保存，请完成后再试' : unconfirmed.current ? '上次保存结果尚未确认，请先刷新核对' : copyPending ? '正在固定复制快照，请稍候' : status !== 'ready' ? '画布尚未连接' : quick || draft || editing ? '请先完成或取消当前编辑；输入框内的快捷键仅撤销文字编辑' : memberPanel || fileObject ? '请先关闭当前面板' : branchPreview || connection.preview ? '请先结束或取消拖动/拉线' : historyUnavailable ? '撤销记录暂不可用，请刷新' : !history[direction] ? direction === 'undo' ? '暂无可撤销的画布操作' : '暂无可重做的画布操作；撤销后进行新修改会清空重做' : !history[direction]!.can_apply ? history[direction]!.reason || '知识或权限已更新，旧操作无法恢复' : '';
  const changeHistory = async (direction: 'undo' | 'redo') => {
    const entry = history[direction]; const reason = historyReason(direction); if (reason || !entry) { setHistoryNotice(reason); return; }
    setHistoryNotice('');
    const owner = scene.containers.find(c => c.id === entry.container_id); const kind = owner?.kind ?? entry.container_kind;
    if (kind && entry.container_id) { setTab(kind); setProject(entry.container_id); setSelected(null); setMulti(new Set()); }
    const before = sceneRef.current, epoch = browseEpoch.current;
    const applied = await act(direction + '_canvas', { history_id: entry.history_id });
    if (applied && browseEpoch.current === epoch) {
      const after = sceneRef.current;
      const related = historyTargets(before, after, entry.container_id);
      const priorFrames=new Map(before.nodes.flatMap(n=>boundaryEntries(n.canvas_data).map(b=>[`${n.id}:${b.key}`,JSON.stringify(b.group)] as const)));
      const restoredFrame=entry.operation==='update_canvas_data'?after.nodes.flatMap(n=>boundaryEntries(n.canvas_data).filter(b=>priorFrames.get(`${n.id}:${b.key}`)!==JSON.stringify(b.group)).map(b=>({node:n.id,kind:'boundary' as const,key:b.key})))[0]:undefined;
      if (related.length) {
        const owner = after.containers.find(c => c.id === related[0].container_id);
        if (owner) { setTab(owner.kind); setProject(owner.id); }
        const restoredRelation = ['connect', 'delete_relation', 'reconnect_relation','update_relation','create_relation_topic'].includes(entry.operation) ? after.relations.find(r => { const old = before.relations.find(p => p.id === r.id); return !old || old.source_object_id !== r.source_object_id || old.target_object_id !== r.target_object_id || JSON.stringify(old.appearance)!==JSON.stringify(r.appearance); }) : undefined;
        setSelectedRelation(restoredRelation?.id ?? null);
        setSelected(restoredRelation ? null : related[0].id); setMulti(new Set(restoredRelation ? [] : related.map(n => n.id)));
        setFolded(previous => { const next = new Set(previous); for (const n of related) { let p = summaryParent(after.nodes,n); const visited = new Set<string>(); while (p && !visited.has(p)) { visited.add(p); next.delete(p); const parent=after.nodes.find(a=>a.id===p);p=parent?summaryParent(after.nodes,parent):null; } } return next; });
        setHistoryFocus(related.map(n => n.id));
        setNavigationTick(t => t + 1);
      }
      if(restoredFrame){setSelected(null);setMulti(new Set());setGroupSelection(restoredFrame);}
    }
    if (applied) setHistoryNotice('已' + (direction === 'undo' ? '撤销' : '重做') + '：' + (historyLabels[entry.operation] ?? '画布操作'));
    if (browseEpoch.current === epoch) focusCanvas();
  };
  const chosen = new Set([...multi, ...(selected ? [selected] : [])].filter(id => byId.has(id)));
  const branchIds = () => branchScope(nodes,chosen);
  const drawnNodes = nodeMotion.project(nodes).map(n=>widthPreview?.id===n.id?{...n,x:widthPreview.x,y:widthPreview.y}:n);
  const drawnById = new Map(drawnNodes.map(n => [n.id, n]));
  useEffect(()=>{if(imageView){const image=scene.nodes.find(n=>n.id===imageView.id)?.canvas_data?.image;if(!image||image.binding_id+':'+image.revision!==imageView.key)setImageView(null);}},[scene.nodes,imageView]);
  const visibleNodes = drawnNodes.filter(n => !hidden(n));
  const departingIds = new Set(presence.leaving.map(n => n.id)), liveVisibleIds = new Set(visibleNodes.map(n => n.id));
  const visibleRelations = scene.relations.filter(r => r.container_id === active?.id && visibleNodes.some(n => n.id === r.source_object_id) && visibleNodes.some(n => n.id === r.target_object_id));
  const groupNode=groupSelection?byId.get(groupSelection.node):undefined;
  const activeGroup=groupSelection&&groupNode?readGroup(groupNode,groupSelection):undefined;
  useEffect(()=>{if(selected||multi.size)setGroupSelection(null);},[selected,multi]);
  useEffect(()=>{setGroupSelection(g=>{const n=g?scene.nodes.find(n=>n.id===g.node&&n.container_id===active?.id):undefined;return g&&n&&readGroup(n,g)?g:null;});},[active?.id,scene.nodes]);
  const selectGroup=(group:GroupSelection)=>{if(browseBlocked)return;setGroupSelection(group);setSelected(null);setMulti(new Set());setSelectedRelation(null);setMenu(null);focusCanvas();};
  const editGroup=(group=groupSelection)=>{const n=group?byId.get(group.node):undefined;if(!n||!group||!writable(n)||historyBlocked)return;if(group.kind==='summary'){setGroupSelection(null);rename(n);}else openVisual('boundary',n,group.key);};
  const deleteGroup=async(group=groupSelection)=>{const n=group?byId.get(group.node):undefined;if(!n||!group||!writable(n)||historyBlocked)return;const ok=group.kind==='boundary'?await saveAnnotations([{node:n,data:setBoundary(n.canvas_data,group.key??'legacy',null)}]):await act('delete_branches',{container_id:n.container_id,expected_container_revision:container(n.container_id)!.revision,objects:[{object_id:n.id,expected_revision:n.revision}]});if(ok)setGroupSelection(null);setMenu(null);focusCanvas();};
  const currentRelation = visibleRelations.find(r => r.id === selectedRelation);
  useEffect(() => { if (selected || multi.size || !currentRelation) setSelectedRelation(null); }, [selected, multi, currentRelation]);
  const selectRelation = (id: string) => {
    if (browseBlocked) return;
    browseEpoch.current++; suppressReveal.current = false;
    setGroupSelection(null); setMulti(new Set()); setSelected(null); setSelectedRelation(id); setMenu(null); focusCanvas();
  };
  const deleteRelation = async () => {
    if (historyBlocked || !currentRelation || !active || active.role === 'viewer') return;
    if (await act('delete_relation', { container_id: currentRelation.container_id, expected_container_revision: active.revision, relation_id: currentRelation.id })) setSelectedRelation(null);
    focusCanvas();
  };
  const selectionPayload = () => {
    const items = nodes.filter(n => chosen.has(n.id)); const cid = items[0]?.container_id;
    if (!items.length || items.some(n => n.container_id !== cid || !writable(n))) { setError('请选择同一空间内可编辑的分支'); return null; }
    return { container_id: cid, expected_container_revision: container(cid)!.revision,
      objects: items.map(n => ({ object_id: n.id, expected_revision: n.revision })) };
  };
  const copySelection = (mode: 'copy' | 'cut' = 'copy') => {
    if (historyBlocked) return null;
    const payload = selectionPayload(); if (!payload) return null;
    const record: BranchClipboard = { mode, token: crypto.randomUUID(), identity, container: payload.container_id, revision: payload.expected_container_revision,
      objects: nodes.filter(n => branchIds().has(n.id)).map(n => ({ object_id: n.id, expected_revision: n.revision })) };
    if (record.objects.length > 1000) { setError('单次最多复制 1000 个节点，请分批选择'); return null; }
    if (clipboard?.mode === 'cut' && clipboard.token) cancelledCuts.current.add(clipboard.token);
    if (mode === 'cut') liveCuts.current.add(record.token!);
    else {
      record.snapshot_id = record.token; record.snapshot_nodes = structuredClone(nodes.filter(n => branchIds().has(n.id)));
      setCopyPending(true); const key = identity; const generation = copyGeneration.current;
      const ready = knowledgeApi.captureSnapshot({ snapshot_id: record.token, ...payload, objects: record.objects }).then(() => true).catch(e => { if (identityRef.current === key && copyGeneration.current === generation) { setError('复制快照未完成：' + message(e) + '。请重新复制。'); setClipboard(current => current?.token === record.token ? null : current); } return false; }).finally(() => { if (identityRef.current === key && copyGeneration.current === generation) setCopyPending(false); });
      snapshotReady.current.set(record.token!, ready);
    }
    setClipboard(record); setMenu(null); return record;
  };
  const copyToSystem = (mode: 'copy' | 'cut' = 'copy') => {
    const record = copySelection(mode); if (!record) return;
    viewport.current?.focus({ preventScroll: true });
    // Explicit copy also works when the browser dispatches the event on body.
    let written = false;
    const write = (event: ClipboardEvent) => {
      if (!event.clipboardData) return;
      event.preventDefault(); event.stopPropagation(); event.clipboardData.setData('application/x-everydayai-branches', JSON.stringify(record));
      event.clipboardData.setData('text/plain', nodes.filter(n => branchIds().has(n.id)).map(n => n.title).join('\n')); written = true;
    };
    document.addEventListener('copy', write, true);
    try { document.execCommand?.('copy'); } finally { document.removeEventListener('copy', write, true); }
    if (!written) setError('浏览器未允许写入系统剪贴板，已保留分支；请点击粘贴分支。');
  };
  const pasteSelection = async (record = clipboard, destination?: Menu) => {
    if ((historyBlocked && !copyPending) || !record || record.identity !== identity) return;
    const epoch = browseEpoch.current;
    const identityAtPaste = identity; const generationAtPaste = copyGeneration.current;
    if (record.mode !== 'cut') {
      if (!record.snapshot_id || !Array.isArray(record.snapshot_nodes) || !record.snapshot_nodes.length || record.snapshot_nodes.length > 1000 || record.snapshot_nodes.some(n => !n || typeof n.id !== 'string' || !Number.isFinite(n.x) || !Number.isFinite(n.y) || (n.parent_id !== null && typeof n.parent_id !== 'string'))) { setError('旧复制记录没有内容快照，请重新复制'); return; }
      const ready = snapshotReady.current.get(record.snapshot_id); if (ready && !await ready) return;
      if (identityRef.current !== identityAtPaste || copyGeneration.current !== generationAtPaste) return;
    }
    if (record.mode === 'cut' && (!record.token || !liveCuts.current.has(record.token) || cancelledCuts.current.has(record.token))) { setError('这次剪切已完成或取消，请重新选择分支（页面刷新后需重新剪切）'); return; }
    let target = destination ? (destination.parent ? byId.get(destination.parent) : undefined) : selected ? byId.get(selected) : undefined;
    if (active?.id !== record.container) { setError('请在原知识空间内粘贴分支'); return; }
    if (target && target.container_id !== record.container) { setError('请在原知识空间内选择粘贴位置'); return; }
    let copyRevision = clipboard?.token === record.token && clipboard?.mode === record.mode && clipboard?.container === record.container && JSON.stringify(clipboard.objects) === JSON.stringify(record.objects) ? clipboard.revision : record.revision;
    const source = record.mode === 'cut' ? nodes.filter(n => record.objects.some(o => o.object_id === n.id)) : record.snapshot_nodes!;
    if (record.mode !== 'cut') {
      try { const fresh = await refresh(); if (identityRef.current !== identityAtPaste || copyGeneration.current !== generationAtPaste) return; const scope = fresh.containers.find(c => c.id === record.container && c.role !== 'viewer'); if (!scope) { setError('当前没有编辑权限，无法粘贴快照'); return; } copyRevision = scope.revision; if (target) { const currentTarget = fresh.nodes.find(n => n.id === target!.id && n.container_id === record.container); if (!currentTarget) { setError('粘贴目标已更新，请重新选择位置'); return; } target = currentTarget; } } catch (e) { if (identityRef.current === identityAtPaste && copyGeneration.current === generationAtPaste) setError(message(e)); return; }
    }
    const roots = source.filter(n => !source.some(p => p.id === n.parent_id));
    if (record.mode === 'cut' && target && source.some(n => n.id === target.id)) { setError('不能将分支粘贴到自身或后代，请选择其他节点或空白处'); return; }
    if (!roots.length) { setError('复制的分支已变化，请重新复制'); return; }
    const left = Math.min(...source.map(n => n.x)), top = Math.min(...source.map(n => n.y));
    const bottom = Math.max(0, ...sceneRef.current.nodes.filter(n => n.container_id === record.container).map(n => n.y + 194));
    const occupied = sceneRef.current.nodes.filter(n => n.container_id === record.container && !(record.mode === 'cut' && source.some(s => s.id === n.id)));
    const sizes = contentSizes([...occupied, ...source]);
    const wanted = target ? { x: target.x + 330, y: target.y } : destination?.point ?? { x: left + 40, y: bottom };
    const position = placeContent(source, occupied, sizes, wanted);
    const before = new Set(sceneRef.current.nodes.map(n => n.id));
    const structured=target?planStructuredPaste(source,occupied,target,sizes,record.mode==='cut',active?.canvas_theme?.layout,active?.canvas_theme):null;
    const payload = record.mode === 'cut' ? {
      container_id: record.container, expected_container_revision: copyRevision, objects: record.objects,
      target_id: target?.id ?? null, placement: target ? 'child' : 'root',
      nodes: structured?[...structured.sourceNodes,...structured.nodes]:source.map(n => ({ node_id: n.node_id, expected_revision: n.layout_revision, position: { x: n.x + position.x - left, y: n.y + position.y - top } })),
    } : { snapshot_id: record.snapshot_id, container_id: record.container, expected_container_revision: copyRevision, objects: record.objects, parent_id: target?.id ?? null, offset: { x: position.x - left, y: position.y - top },...(structured?{snapshot_positions:structured.positions,nodes:structured.nodes}:{}) };
    const pasted = await act(record.mode === 'cut' ? 'move_branches' : 'copy_branches', payload);
    if (pasted && record.mode === 'cut') { if (record.token) cancelledCuts.current.add(record.token); setClipboard(null); }
    if (pasted && browseEpoch.current === epoch) {
      const added = record.mode === 'cut' ? sceneRef.current.nodes.filter(n => roots.some(r => r.id === n.id)) : sceneRef.current.nodes.filter(n => n.container_id === record.container && !before.has(n.id));
      const addedRoots = added.filter(n => !added.some(p => p.id === n.parent_id));
      setMulti(new Set(addedRoots.map(n => n.id))); if (addedRoots[0]) { if (destination) setSelected(addedRoots[0].id); else locate(addedRoots[0].id); }
      if (record.mode !== 'cut') setClipboard({ ...record, revision: sceneRef.current.containers.find(c => c.id === record.container)!.revision });
      if (target) setFolded(old => { const next = new Set(old); next.delete(target.id); return next; });
    }
    setMenu(null);
  };
  const setTopicWidth=async(n:KnowledgeNode,width:number|null)=>{const original=sceneRef.current.nodes.find(v=>v.id===n.id);if(original)await saveAnnotations([{node:original,data:{...original.canvas_data,width}}]);focusCanvas();};
  const setTopicSize=async(n:KnowledgeNode,size:TopicResize|null)=>{
    const original=sceneRef.current.nodes.find(v=>v.id===n.id),epoch=browseEpoch.current;
    if(original&&active){
      const data={...original.canvas_data,width:size?.width??null,height:size?.height??null};
      const moved=!!size&&(size.dx!==0||size.dy!==0);
      if(moved&&!data.manual_layout)data.manual_layout={};
      if(moved||JSON.stringify(data)!==JSON.stringify(original.canvas_data))await act('update_canvas_data',{
        container_id:original.container_id,expected_container_revision:container(original.container_id)!.revision,
        updates:[{object_id:original.id,expected_layout_revision:original.layout_revision,canvas_data:data}],
        ...(moved?{nodes:[{node_id:original.node_id,expected_revision:original.layout_revision+1,position:{x:original.x+size.dx,y:original.y+size.dy}}]}:{})});
    }
    nodeMotion.cancel();setWidthPreview(null);
    if(browseEpoch.current===epoch)focusCanvas();
  };
  const deleteSingle = async (n:KnowledgeNode) => {
    if(historyBlocked||!writable(n))return;
    const current=sceneRef.current.nodes.filter(v=>v.container_id===n.container_id),plan=removeTopic(current,n,contentSizes(current),active?.canvas_theme?.layout,active?.canvas_theme),epoch=browseEpoch.current;
    const ok=await act('delete_topic',{container_id:n.container_id,expected_container_revision:container(n.container_id)!.revision,object_id:n.id,expected_revision:n.revision,nodes:structureMoves(current,plan.nodes)});
    if(ok&&browseEpoch.current===epoch){setMulti(new Set());setSelected(plan.children[0]?.id??n.parent_id);focusCanvas();}setMenu(null);
  };
  const reorderSingle=async(n:KnowledgeNode,step:-1|1)=>{
    if(historyBlocked||!writable(n))return;
    const current=sceneRef.current.nodes.filter(v=>v.container_id===n.container_id),plan=reorderTopic(current,n,step,contentSizes(current),active?.canvas_theme?.layout,active?.canvas_theme),epoch=browseEpoch.current;if(!plan)return;
    if(await act('move_branch',{object_id:n.id,expected_revision:n.revision,expected_container_revision:container(n.container_id)!.revision,target_id:plan.target,placement:plan.placement,nodes:plan.changed.map(v=>({node_id:v.node_id,expected_revision:v.layout_revision,position:{x:v.x,y:v.y}}))})&&browseEpoch.current===epoch){setSelected(n.id);setMulti(new Set());setNavigationTick(t=>t+1);focusCanvas();}
  };
  const deleteSelection = async () => {
    if (historyBlocked) return;
    const payload = selectionPayload(); if (!payload) return; const epoch = browseEpoch.current;
    const removed=branchScope(nodes,chosen),remaining=reconcileSummaries(nodes,nodes.filter(n=>!removed.has(n.id))),sizes=contentSizes(nodes);
    let arranged=followSummaryPositions(nodes,remaining,sizes);
    for(const root of arranged.filter(n=>(n.canvas_data?.structure||Object.values(active?.canvas_theme?.layout??{}).some(Boolean))&&!structureRoot(arranged,summaryParent(arranged,n)??'',active?.canvas_theme?.layout)))arranged=arrangeStructure(arranged,root.id,sizes,active?.canvas_theme?.layout,active?.canvas_theme);
    const moves=structureMoves(nodes,arranged);
    if (await act('delete_branches', {...payload,...(nodes.some(n=>n.canvas_data?.summary)&&moves.length?{nodes:moves}:{})}) && browseEpoch.current === epoch) { setMulti(new Set()); setSelected(null); }
    setMenu(null); if (browseEpoch.current === epoch) focusCanvas();
  };
  useEffect(()=>{setRelationText(null);setMapEditor(null);setWidthPreview(null);setMediaEditor(null);setMediaView(null);setVisual(null);setStyleClipboard(undefined);setBrush(false);},[identity]);
  const relationLabelDraft=useCallback((value:RelationLabelDraft|null)=>setRelationText(old=>value?{...value,containerRevision:old?.relation.id===value.relation.id?old.containerRevision:sceneRef.current.containers.find(c=>c.id===value.relation.container_id)?.revision}:null),[]);
  const visualDraft=useCallback((draft:VisualDraft)=>setVisual(v=>v&&JSON.stringify(v.draft)!==JSON.stringify(draft)?{...v,draft}:v),[]);
  const annotationDraft=useCallback((draft:AnnotationDraft)=>setAnnotation(v=>v&&JSON.stringify(v.draft)!==JSON.stringify(draft)?{...v,draft}:v),[]);
  const mediaDraft=useCallback((draft:MediaDraft)=>setMediaEditor(v=>v&&JSON.stringify(v.draft)!==JSON.stringify(draft)?{...v,draft}:v),[]);
  const toolNodes = nodes.filter(n => chosen.has(n.id));
  const openMedia=(mode:'video'|'link',node=toolNodes[0])=>{if(node&&!historyBlocked&&writable(node)){setError('');setMediaEditor({node,mode,containerRevision:container(node.container_id)?.revision});}};
  const openVisual = (mode: VisualMode, node = toolNodes[0],boundaryKey?:string) => { if(node && !historyBlocked && writable(node))setVisual({node,mode,boundaryKey,containerRevision:container(node.container_id)?.revision,targets:mode==='style'&&chosen.has(node.id)?toolNodes:[node]}); };
  const openAnnotation = (mode: AnnotationMode, node = toolNodes[0]) => { if (node && !pending && !quick) { setAnnotation({ node, mode,containerRevision:container(node.container_id)?.revision }); setMenu(null); } };
  const saveAnnotations = async (updates: { node: KnowledgeNode; data: CanvasData }[]) => {
    if (!active || active.role === 'viewer') return false;
    const current = sceneRef.current.containers.find(c=>c.id===active.id)!;
    const existing=sceneRef.current.nodes.filter(n=>n.container_id===active.id),sizes=contentSizes(existing);
    for(const {node,data} of updates){
      const beforeData=mapAppearance(node,existing,active?.canvas_theme),afterData=mapAppearance({...node,canvas_data:data},existing,active?.canvas_theme);if(!visualSizeChanged(beforeData,afterData))continue;
      const el=viewport.current?.querySelector<HTMLElement>(`[data-node-id="${node.id}"]`),clone=el?.cloneNode(true) as HTMLElement|undefined;
      let height=titleHeight(node.title,data.width??272,afterData?.style?.font_size??15)+(data.image?data.image.width/(data.image.aspect??1):0)+(data.video?84:0)+(data.link?84:0);
      if(clone){clone.removeAttribute('data-node-id');clone.inert=true;Object.assign(clone.style,mapNodeStyle({...node,canvas_data:data},existing,active?.canvas_theme),{width:(data.width??272)+'px',boxSizing:'border-box',visibility:'hidden',pointerEvents:'none',left:'0',top:'0',transform:'none'});const heading=clone.querySelector('h3');if(heading){heading.removeAttribute('style');Object.assign(heading.style,titleStyle(afterData));paintTitle(heading as HTMLElement,node.title,data.title_format);}clone.querySelector('.kc-image-wrap')?.remove();clone.querySelector('.kc-media-cards')?.remove();if(data.video||data.link){const media=document.createElement('div');media.style.height=((data.video?84:0)+(data.link?84:0))+'px';clone.append(media);}if(data.image){const placeholder=document.createElement('div');placeholder.style.height=(data.image.width/(data.image.aspect??1))+'px';clone.append(placeholder);}viewport.current?.append(clone);height=clone.offsetHeight||height;clone.remove();}
      sizes.set(node.id,{width:data.width??272,height});
    }
    const next=existing.map(n=>{const update=updates.find(u=>u.node.id===n.id);return update?{...n,canvas_data:update.data}:n;}),groupChange=updates.some(({node,data})=>JSON.stringify([node.canvas_data?.summary,node.canvas_data?.boundary,node.canvas_data?.boundaries])!==JSON.stringify([data.summary,data.boundary,data.boundaries]));
    const needsLayout=groupChange||updates.some(({node,data})=>visualSizeChanged(mapAppearance(node,existing,active?.canvas_theme),mapAppearance({...node,canvas_data:data},existing,active?.canvas_theme)));
    let arranged=needsLayout?reflowContent(next,sizes):next;
    if(needsLayout){const roots=new Set(next.filter(n=>(n.canvas_data?.structure||Object.values(active?.canvas_theme?.layout??{}).some(Boolean))&&!structureRoot(next,summaryParent(next,n)??'',active?.canvas_theme?.layout)).map(n=>n.id));for(const u of updates){if(!visualSizeChanged(u.node.canvas_data,u.data))continue;let root=next.find(n=>n.id===u.node.id);const seen=new Set<string>();while(root?.parent_id&&!seen.has(root.id)){seen.add(root.id);root=next.find(n=>n.id===root!.parent_id)??root;}if(root)roots.add(root.id);}if(groupChange)for(const u of updates){const member=u.data.summary?.members[0]??u.data.boundary?.members[0];const parent=next.find(n=>n.id===member)?.parent_id;if(parent)roots.add(structureRoot(next,parent,active?.canvas_theme?.layout)?.id??parent);}for(const id of roots)arranged=arrangeStructure(arranged,id,sizes,active?.canvas_theme?.layout,active?.canvas_theme);}
    const modified=new Set(updates.map(u=>u.node.id));
    const moves=arranged.filter(n=>{const old=existing.find(o=>o.id===n.id)!;return n.x!==old.x||n.y!==old.y;}).map(n=>({node_id:n.node_id,expected_revision:n.layout_revision+(modified.has(n.id)?1:0),position:{x:n.x,y:n.y}}));
    return act('update_canvas_data', { container_id: active.id, expected_container_revision: current.revision, updates: updates.map(({node,data}) => ({ object_id:node.id,expected_layout_revision:node.layout_revision,canvas_data:data })),...(moves.length?{nodes:moves}:{}) });
  };
  const saveMapTheme=async(level?:{depth:number;style:NonNullable<CanvasData['style']>})=>{
    if(!mapEditor||!active||active.id!==mapEditor.container||active.role==='viewer')return false;
    const current=sceneRef.current.nodes.filter(n=>n.container_id===active.id),theme=level?updateMapLevel(mapEditor.theme,level.depth,level.style):mapEditor.theme;
    const updates=current.filter(n=>(mapEditor.reset||!!level&&topicDepth(current,n)===level.depth)&&!!n.canvas_data?.style).map(n=>({object_id:n.id,expected_layout_revision:n.layout_revision,style:null}));
    const modified=new Set(updates.map(u=>u.object_id)),next=current.map(n=>modified.has(n.id)?{...n,canvas_data:{...n.canvas_data,style:null}}:n),sizes=contentSizes(current);
    const layoutChanged=JSON.stringify(theme.layout)!==JSON.stringify(active.canvas_theme?.layout);
    const metricChanged=layoutChanged||next.some(n=>visualSizeChanged(mapAppearance(current.find(o=>o.id===n.id)!,current,active.canvas_theme),mapAppearance(n,next,theme)));
    if(metricChanged)for(const n of next){const el=viewport.current?.querySelector<HTMLElement>(`[data-node-id="${n.id}"]`),clone=el?.cloneNode(true) as HTMLElement|undefined,data=mapAppearance(n,next,theme),font=data?.style?.font_size??15;let height=titleHeight(n.title,topicWidth({...n,canvas_data:data}),font)+(n.canvas_data?.image?n.canvas_data.image.width/(n.canvas_data.image.aspect??1):0)+(n.canvas_data?.video?84:0)+(n.canvas_data?.link?84:0);
      if(clone){clone.removeAttribute('data-node-id');clone.inert=true;Object.assign(clone.style,mapNodeStyle(n,next,theme),{visibility:'hidden',pointerEvents:'none',left:'0',top:'0',transform:'none'});const heading=clone.querySelector<HTMLElement>('h3');if(heading){heading.removeAttribute('style');Object.assign(heading.style,titleStyle(data));}viewport.current?.append(clone);height=clone.offsetHeight||height;clone.remove();}sizes.set(n.id,{width:topicWidth({...n,canvas_data:data}),height});}
    let arranged=metricChanged&&!layoutChanged?reflowContent(next,sizes):next;
    if(metricChanged)for(const root of next.filter(n=>layoutChanged?!summaryParent(next,n):(n.canvas_data?.structure||Object.values(active?.canvas_theme?.layout??{}).some(Boolean))&&!structureRoot(next,summaryParent(next,n)??'',active?.canvas_theme?.layout)))arranged=arrangeStructure(arranged,root.id,sizes,theme.layout,theme);
    const ok=await act('update_map_theme',{container_id:mapEditor.container,expected_container_revision:mapEditor.revision,theme,updates,nodes:structureMoves(current,arranged,modified)});
    if(ok){setMapEditor(null);focusCanvas();}return ok;
  };
  const createDefaultTopic=(context:{point:Point;parent:string|null},structure?:TopicStructure)=>{
    if(historyBlocked||!active||active.role==='viewer')return;
    if(context.parent&&(!byId.has(context.parent)||!writable(byId.get(context.parent)!)))return;
    const position=freePosition(context.point);cancelQuick.current=false;setError('');setMenu(null);
    setQuick({node:null,container:active.id,parent:context.parent,position,title:'',structure});
    if(context.parent)setFolded(old=>{const next=new Set(old);next.delete(context.parent!);return next;});revealQuick(position);
  };
  const changeStructure=async(node:KnowledgeNode,structure:TopicStructure)=>{
    if(historyBlocked||!writable(node))return;
    setMenu(null);
    const current=sceneRef.current.nodes.filter(n=>n.container_id===node.container_id);
    const next=current.map(n=>n.id===node.id?{...n,canvas_data:{...n.canvas_data,structure}}:n);
    const root=structureRoot(next,node.id,active?.canvas_theme?.layout)!;
    const arranged=arrangeStructure(next,root.id,contentSizes(current),active?.canvas_theme?.layout,active?.canvas_theme);
    const owner=sceneRef.current.containers.find(c=>c.id===node.container_id)!,epoch=browseEpoch.current;
    if(await act('update_canvas_data',{container_id:owner.id,expected_container_revision:owner.revision,updates:[{object_id:node.id,expected_layout_revision:node.layout_revision,canvas_data:{...node.canvas_data,structure}}],nodes:structureMoves(current,arranged,new Set([node.id]))})&&browseEpoch.current===epoch){setSelected(node.id);setMulti(new Set());setNavigationTick(t=>t+1);focusCanvas();}
  };
  const addGroup = async (mode: 'summary' | 'boundary') => {
    if (historyBlocked || !active || active.role === 'viewer') return;
    const groups = selectedGroups(nodes, chosen);
    if (!groups.length) { setError('请选择有共同父节点的子分支；根主题不参与概要或外框'); return; }
    const epoch=browseEpoch.current;
    if (mode === 'boundary') {
      const updates=groups.map(group=>({node:group[0],data:appendBoundary(group[0].canvas_data,{title:'外框',members:group.map(n=>n.id)})}));
      if(await saveAnnotations(updates)&&browseEpoch.current===epoch){setSelected(null);setMulti(new Set());setGroupSelection({node:updates[0].node.id,kind:'boundary',key:updates[0].data.boundaries!.at(-1)!.id});}
    } else {
      const sizes = contentSizes(nodes),reserved=nodes.map(n=>({...n,...sizes.get(n.id)!}));
      const ok=await act('create_summaries', {container_id:active.id,expected_container_revision:active.revision,groups:groups.map(group => { const bounds = groupBounds(nodes,group.map(n=>n.id),sizes)!;const position=placeSummary(bounds,branchDirection(nodes,group[0].id),reserved);reserved.push({...group[0],...position,width:272,height:170});return {members:group.map(n=>n.id),title:'概要',position}; })});
      if(ok&&browseEpoch.current===epoch){setMulti(new Set());setSelected(null);const made=createdNode.current as KnowledgeNode|null;if(made)setGroupSelection({node:made.id,kind:'summary'});}
    }
    if(browseEpoch.current===epoch)focusCanvas();
  };
  const connectSelected = (relation_type = 'related') => {
    if (![1,2].includes(toolNodes.length) || historyBlocked) return;
    if(toolNodes.length===1){cancelView();connection.beginRelation(toolNodes[0].id,relation_type);return;}
    void act('connect',{source_object_id:toolNodes[0].id,target_object_id:toolNodes[1].id,relation_type});
  };
  const nativeEditing = (target: EventTarget) => !(target as Element).closest('.kc-page') || (target as Element).closest('input,textarea,select,[contenteditable],[role="dialog"],[role="menu"],.kc-file-panel,.kc-source,#kc-history-help');
  const uploadNodeImage = async (node: KnowledgeNode, file: File) => {
    if(historyBlocked || !writable(node) || !/^image\/(png|jpeg|webp|gif)$/.test(file.type))return;
    const key=identity;setFilePending(true);
    try { const result=await knowledgeFilesApi.upload(node.id,file,crypto.randomUUID());if(identityRef.current!==key)return;await refresh();if(identityRef.current!==key)return;
      const binding=(result.data as {binding_id?:string}).binding_id;
      const list=await knowledgeFilesApi.list(node.id);const f=list.find(f=>f.id===binding);if(!f)throw new Error('图片上传成功，但未能读取绑定，请到文件资料检查');
      await saveAnnotations([{node,data:{...node.canvas_data,image:{binding_id:f.id,revision:f.current_revision,width:220,placement:'bottom',aspect:await imageAspect(file),alt:file.name}}}]);
    } catch(e) { if(identityRef.current===key)setError(message(e)); } finally {if(identityRef.current===key)setFilePending(false);}
  };
  const insertTextTopics=async(text:string)=>{
    if(historyBlocked||!active||active.role==='viewer')return;
    if(chosen.size>1){setError('请只选择一个父主题，或取消选择后粘贴');return;}
    const target=chosen.size===1?byId.get([...chosen][0]):undefined;if(target&&(!writable(target)||target.container_id!==active.id))return;
    try{
      const topics=parseTopicOutline(text);if(!topics.length)return;
      const current=sceneRef.current.nodes.filter(n=>n.container_id===active.id),rect=viewport.current?.getBoundingClientRect(),anchor=rect?point(rect.left+rect.width/2,rect.top+rect.height/2):{x:100,y:150};
      const plan=planTopicOutline(topics,current,active.id,target,anchor,contentSizes(current),active.canvas_theme?.font_size??15,active.canvas_theme);
      if(!target){const position=placeContent(plan.virtual,current,plan.sizes,{x:Math.min(...plan.virtual.map(n=>n.x)),y:Math.min(...plan.virtual.map(n=>n.y))});const dx=position.x-Math.min(...plan.virtual.map(n=>n.x)),dy=position.y-Math.min(...plan.virtual.map(n=>n.y));for(const topic of plan.topics){topic.position.x+=dx;topic.position.y+=dy;}}
      const before=new Set(current.map(n=>n.id)),epoch=browseEpoch.current;
      if(await act('create_topics',{container_id:active.id,expected_container_revision:sceneRef.current.containers.find(c=>c.id===active.id)!.revision,parent_id:target?.id??null,topics:plan.topics,nodes:plan.nodes})&&browseEpoch.current===epoch){
        const roots=sceneRef.current.nodes.filter(n=>n.container_id===active.id&&!before.has(n.id)&&n.parent_id===(target?.id??null));setMulti(new Set(roots.map(n=>n.id)));if(roots[0])setSelected(roots[0].id);if(target)setFolded(old=>{const next=new Set(old);next.delete(target.id);return next;});setHistoryFocus(roots.map(n=>n.id));focusCanvas();
      }
    }catch(e){setError(message(e));}
  };
  const pasteEvent = (e: Pick<ClipboardEvent, 'target' | 'clipboardData' | 'preventDefault'>) => {
    if (!e.target || nativeEditing(e.target) || (historyBlocked && !copyPending)) return;
    const image=Array.from(e.clipboardData?.files??[]).find(f=>/^image\/(png|jpeg|webp|gif)$/.test(f.type));if(image&&toolNodes.length===1){e.preventDefault();void uploadNodeImage(toolNodes[0],image);return;}
    const data = e.clipboardData?.getData('application/x-everydayai-branches');
    if (!data){const text=e.clipboardData?.getData('text/plain')??'';if(text.split(/\r\n?|\n/).filter(line=>line.trim()).length>1){e.preventDefault();void insertTextTopics(text);}return;}
    e.preventDefault();
    try { const record = JSON.parse(data); if (record.identity === identity && Array.isArray(record.objects) && record.objects.length <= 1000) void pasteSelection(record); }
    catch { setError('无法读取分支，请重新复制'); }
  };
  useEffect(() => {
    const paste = (event: ClipboardEvent) => { if (!event.defaultPrevented && document.activeElement === viewport.current) pasteEvent({ target: viewport.current, clipboardData: event.clipboardData, preventDefault: () => event.preventDefault() }); };
    document.addEventListener('paste', paste); return () => document.removeEventListener('paste', paste);
  });
  const onKey = (e: React.KeyboardEvent) => {
    if ((e.target as Element).closest('button,a,[role=button]') && ['Enter',' '].includes(e.key)) return;
    if(e.key==='Shift'&&drag.current?.type==='node'&&drag.current.moved){drag.current.snapDisabled=true;drag.current.snapLocks=undefined;if(drag.current.cursor)updateGesture(drag.current.cursor);return;}
    if (e.defaultPrevented || menu || !e.currentTarget.contains(e.target as Node) || (e.target as Element).closest('[role="menu"],[role="dialog"],.kc-file-panel,.kc-source')) return;
    const key = e.key.toLowerCase();
    if(groupSelection&&!nativeEditing(e.target)&&!browseBlocked&&!e.nativeEvent.isComposing&&!e.repeat){if(e.key==='Escape'){e.preventDefault();setGroupSelection(null);focusCanvas();return;}if(['Delete','Backspace'].includes(e.key)&&!e.ctrlKey&&!e.metaKey&&!e.altKey){e.preventDefault();void deleteGroup();return;}if(['Enter',' ','F2'].includes(e.key)&&!e.ctrlKey&&!e.metaKey&&!e.altKey){e.preventDefault();editGroup();return;}}
    const typeTarget=chosen.size===1&&!selectedRelation?byId.get([...chosen][0]):undefined,typingScope=!!(e.target as Element).closest('.kc-workspace,.kc-tree-row');
    const inputMethod=e.nativeEvent.isComposing||e.keyCode===229||['Process','Dead'].includes(e.key),altGraph=e.getModifierState?.('AltGraph');
    if(typingScope&&typeTarget&&writable(typeTarget)&&!nativeEditing(e.target)&&!historyBlocked&&!brush&&(!drag.current||(drag.current.type==='node'&&!drag.current.moved))&&!marquee&&!e.repeat&&((!e.ctrlKey&&!e.metaKey&&!e.altKey)||altGraph)&&(inputMethod||([...e.key].length===1&&!!e.key.trim()))){
      if(inputMethod)imeCanvasNode.current=typeTarget.id;else e.preventDefault();flushSync(()=>rename(typeTarget,inputMethod?'':e.key));return;
    }
    if(typingScope&&typeTarget&&writable(typeTarget)&&!nativeEditing(e.target)&&!historyBlocked&&!e.nativeEvent.isComposing&&!e.repeat&&e.key==='Enter'&&(e.metaKey||e.ctrlKey)&&!e.altKey&&!e.shiftKey){e.preventDefault();startParent(typeTarget);return;}

    if(typingScope&&typeTarget&&!nativeEditing(e.target)&&!historyBlocked&&!inputMethod&&!e.repeat){if(e.altKey&&!e.metaKey&&!e.ctrlKey&&!e.shiftKey&&['ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();void reorderSingle(typeTarget,e.key==='ArrowUp'?-1:1);return;}if(!e.altKey&&!e.shiftKey&&((e.metaKey&&!e.ctrlKey&&e.key==='Backspace')||(e.ctrlKey&&!e.metaKey&&e.key==='Delete'))){e.preventDefault();void deleteSingle(typeTarget);return;}}
    if(typingScope&&!nativeEditing(e.target)&&!browseBlocked&&!inputMethod&&!e.repeat){if(e.altKey&&e.shiftKey&&!e.ctrlKey&&!e.metaKey&&['ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();foldAll(e.key==='ArrowDown');return;}if(e.key==='Home'&&!e.altKey&&!e.shiftKey&&!e.ctrlKey&&!e.metaKey){e.preventDefault();goToCenter();return;}}
    if(e.key==='Escape'&&brush){e.preventDefault();setBrush(false);return;}
    if (!nativeEditing(e.target) && !historyBlocked && !e.nativeEvent.isComposing && !e.repeat) {
      if (key === 'b' && !e.altKey && ((e.ctrlKey && !e.metaKey && !e.shiftKey) || (e.metaKey && e.shiftKey && !e.ctrlKey))) { e.preventDefault(); void addGroup('boundary'); return; }
      if (!e.altKey && !e.metaKey && !e.ctrlKey && !e.shiftKey && toolNodes.length === 1 && (e.key === 'F3' || e.key === 'F4')) { e.preventDefault(); openAnnotation(e.key === 'F3' ? 'labels' : 'note'); return; }
    }

    if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && (key === 'c' || key === 'x') && !nativeEditing(e.target) && !historyBlocked && !e.repeat && !e.nativeEvent.isComposing) { e.preventDefault(); copyToSystem(key === 'x' ? 'cut' : 'copy'); return; }
    if ((e.metaKey || e.ctrlKey) && !e.altKey && (key === 'z' || (key === 'y' && e.ctrlKey && !e.metaKey))) {
      if ((e.target as HTMLElement).closest('input,textarea,select,[contenteditable]') || quick || draft || editing || memberPanel || fileObject || e.nativeEvent.isComposing || e.repeat) return;
      e.preventDefault(); void changeHistory(key === 'y' || e.shiftKey ? 'redo' : 'undo'); return;
    }

    if (!nativeEditing(e.target) && !browseBlocked && !e.nativeEvent.isComposing && !e.repeat) {
      if ((e.metaKey || e.ctrlKey) && e.altKey && !e.shiftKey && !historyBlocked && (key==='c'||key==='v')) { e.preventDefault();if(key==='c'&&toolNodes.length===1)setStyleClipboard(styleSnapshot(mapAppearance(toolNodes[0],nodes,active?.canvas_theme)));if(key==='v'&&styleClipboard!==undefined)void saveAnnotations(toolNodes.map(node=>({node,data:pasteTopicStyle(node.title,node.canvas_data,styleClipboard!)})));return; }
      if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && key === 'a') { e.preventDefault(); setSelectedRelation(null); const ids = nodes.filter(n => n.container_id === active?.id && !hidden(n)).map(n => n.id); setMulti(new Set(ids)); setSelected(null); return; }
      if (!historyBlocked && !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey && ['Delete', 'Backspace'].includes(e.key)) { e.preventDefault(); if (currentRelation) void deleteRelation(); else void deleteSelection(); return; }
    }
    const onCanvas = (e.target as HTMLElement).closest('.kc-workspace,.kc-tree-row');
    if (onCanvas && !nativeEditing(e.target) && !browseBlocked && !e.nativeEvent.isComposing && !e.repeat && (e.key === 'ContextMenu' || (e.key === 'F10' && e.shiftKey))) {
      e.preventDefault(); const target = navigationId ? byId.get(navigationId) : undefined;
      const box = (target ? viewport.current?.querySelector<HTMLElement>(`[data-node-id="${target.id}"]`) : viewport.current)?.getBoundingClientRect();
      if (box) { const x = box.left + box.width / 2, y = box.top + box.height / 2; setSelectedRelation(null); setMenu({ x, y, point: point(x, y), parent: target?.id ?? null }); }
      return;
    }

    if (onCanvas && !nativeEditing(e.target) && !browseBlocked && !marquee && !drag.current && !e.nativeEvent.isComposing) {
      if (['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key) && !e.altKey && !e.metaKey && !e.ctrlKey && !e.shiftKey) {
        e.preventDefault(); browseEpoch.current++; suppressReveal.current = false; const next = navigateNode(nodes, navigationId, e.key, folded, active?.id);
        if (next.expand) toggleFold(next.expand, true); if (next.collapse) toggleFold(next.collapse, false);
        setMulti(new Set()); setSelected(next.id); setMenu(null); focusCanvas(); return;
      }
      if (e.key === '/' && (e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && !e.repeat && navigationId && nodes.some(n => n.parent_id === navigationId)) { e.preventDefault(); toggleFold(navigationId); return; }
    }
    if (e.key === 'Escape' && marquee) { edgePan.stop(); marqueeRef.current = null; setMarquee(null); setMulti(marqueeBase.current); return; }
    if (e.key === 'Escape' && drag.current?.type === 'node') { e.preventDefault(); cancelBranch(); return; }
    if (branchPreview) { e.preventDefault(); return; }
    if (connection.preview) { e.preventDefault(); return; }
    if ((e.target as HTMLElement).closest('input,textarea,select,dialog,button') || quick || draft || editing || memberPanel || e.nativeEvent.isComposing) return;
    if (e.code === 'Space' && !browseBlocked && !e.shiftKey) {
      const node = selected ? byId.get(selected) : undefined;
      if (node && historyBlocked) { e.preventDefault(); return; }
      if (node && writable(node) && !e.altKey && !e.ctrlKey && !e.metaKey) { e.preventDefault(); rename(node); return; }
      space.current = true; e.preventDefault();
    }
    if (e.key === 'Escape') { setSelectedRelation(null); if (clipboard?.mode === 'cut') { if (clipboard.token) cancelledCuts.current.add(clipboard.token); setClipboard(null); } setMenu(null); setMulti(new Set()); setSelected(null); }
    const n = selected ? byId.get(selected) : undefined;
    if (!historyBlocked && !e.repeat && !e.altKey && !e.ctrlKey && !e.metaKey && n && writable(n) && ['Tab', 'Enter', 'F2'].includes(e.key) && (!e.shiftKey || e.key === 'Enter')) {
      e.preventDefault(); if (e.key === 'F2') rename(n);
      else startQuick(n, e.key === 'Tab', false, e.shiftKey && e.key === 'Enter');
    }
  };
  const cancelBranch = () => {
    const d = drag.current; if (d?.type !== 'node') return;
    edgePan.stop(); drag.current = null; setBranchPreview(null);
    nodeMotion.animate(nodeMotion.project(sceneRef.current.nodes), d.base.nodes);
    setFolded(d.foldedBase); foldsRef.current = d.foldedBase; sceneRef.current = d.base; setScene(d.base);
    if (viewport.current?.hasPointerCapture(d.pointer)) viewport.current.releasePointerCapture(d.pointer);
  };
  const pointerDown = (e: ReactPointerEvent, node?: KnowledgeNode) => {
    if(connection.preview?.relationType){e.preventDefault();return;}
    if (brush && node && e.button===0 && !historyBlocked && writable(node)) { e.preventDefault();void saveAnnotations([{node,data:pasteTopicStyle(node.title,node.canvas_data,styleClipboard!)}]);setBrush(false);return; }
    if (drag.current?.type === 'node' && drag.current.moved) return;
    if (browseBlocked || e.button === 2 || (e.target as HTMLElement).closest('button')) return;
    setGroupSelection(null);browseEpoch.current++; suppressReveal.current = false; cancelView();
    const drawn = node ? nodeMotion.coordinates.current?.[node.id] : undefined;
    setSelectedRelation(null);
    if (node && e.button === 0 && !space.current && (e.shiftKey || e.ctrlKey || e.metaKey)) { const next = new Set(chosen); if (next.has(node.id)) next.delete(node.id); else next.add(node.id); setMulti(next); setSelected(null); viewport.current?.focus({ preventScroll: true }); return; }
    if (!node && e.button === 0 && !space.current && !e.altKey) { marqueeBase.current = e.shiftKey ? new Set(chosen) : new Set(); setMulti(marqueeBase.current); setSelected(null); const start = point(e.clientX, e.clientY); marqueeRef.current = { start, end: start }; setMarquee(marqueeRef.current); edgePan.begin(e.pointerId, { x: e.clientX, y: e.clientY }); viewport.current?.setPointerCapture(e.pointerId); viewport.current?.focus({ preventScroll: true }); e.preventDefault(); return; }
    const panning = space.current || e.button === 1 || (e.altKey && !node);
    const sources = node && chosen.has(node.id) && chosen.size > 1 ? [...chosen] : node ? [node.id] : [];
    if (node && (historyBlocked || !writable(node)) && !space.current && e.button !== 1) { setMulti(new Set()); setSelected(node.id); setMenu(null); viewport.current?.focus({ preventScroll: true }); return; }
    if (!space.current && e.button !== 1 && sources.some(id => { const n = byId.get(id)!; return !writable(n) || n.container_id !== node?.container_id; })) { setError('请选择同一空间内可编辑的分支'); return; }
    if (sources.length <= 1 && !panning) setMulti(new Set()); setMenu(null); if (panning) drag.current = { type: 'pan', start: { x: e.clientX, y: e.clientY }, pan };
    else if (node && writable(node)) { if (sources.length <= 1) setSelected(node.id); drag.current = { type: 'node', id: node.id, sources, identity, origin: { x: point(e.clientX, e.clientY).x - ((drawn?.x ?? node.x) - node.x), y: point(e.clientX, e.clientY).y - ((drawn?.y ?? node.y) - node.y) }, foldedBase: new Set(folded), start: { x: e.clientX, y: e.clientY }, position: { x: node.x, y: node.y }, pointer: e.pointerId, base: sceneRef.current, sizes: contentSizes(nodes), visible: new Set(nodes.filter(n => !hidden(n)).map(n => n.id)) }; }
    else if (node) setSelected(node.id); else setSelected(null);
    if (drag.current?.type === 'node') {
      const d = drag.current, moving = branchScope(d.base.nodes,d.sources);
      const visible = nodeMotion.project(d.base.nodes);
      d.offsets = new Map(visible.filter(n => moving.has(n.id)).map(n => { const base = d.base.nodes.find(b => b.id === n.id)!; return [n.id, { x: n.x - base.x, y: n.y - base.y }]; }));
      const next = { ...d.base, nodes: d.base.nodes.map(n => moving.has(n.id) ? visible.find(v => v.id === n.id)! : n) };
      sceneRef.current = next; setScene(next); nodeMotion.detach(moving);
      edgePan.begin(e.pointerId, { x: e.clientX, y: e.clientY });
    }
    if (drag.current) { viewport.current?.focus({ preventScroll: true }); if (drag.current.type === 'pan') { e.preventDefault(); viewport.current?.setPointerCapture(e.pointerId); } }
  };
  const dragPlan = (d: Extract<Drag, { type: 'node' }>, world: Point) => {
    const painted = nodeMotion.project(sceneRef.current.nodes);
    const hit = painted.find(n => { if (!d.visible.has(n.id) || d.sources.includes(n.id)) return false; const size = d.sizes.get(n.id) ?? { width: 272, height: 170 }; return world.x >= n.x - 16 && world.x <= n.x + size.width + 16 && world.y >= n.y - 16 && world.y <= n.y + size.height + 16; });
    const canonical = hit && d.base.nodes.find(n => n.id === hit.id);
    const cursor = hit && canonical ? { x: canonical.x + world.x - hit.x, y: canonical.y + world.y - hit.y } : world;
    const plan=planBranches(d.base.nodes, d.sources, { x: world.x - d.origin.x, y: world.y - d.origin.y }, cursor, d.sizes, new Set(hit ? [hit.id] : []),active?.canvas_theme?.layout,active?.canvas_theme);
    if(plan.placement==='free'&&plan.valid){
      // Retain the painted branch shape when a drag interrupts a layout animation.
      let moved=plan.nodes.map(n=>plan.moving.has(n.id)?{...n,x:n.x+(d.offsets?.get(n.id)?.x??0)-(d.offsets?.get(d.id)?.x??0),y:n.y+(d.offsets?.get(n.id)?.y??0)-(d.offsets?.get(d.id)?.y??0)}:n);
      if(!d.snapDisabled){
        const size=(n:KnowledgeNode)=>({id:n.id,x:n.x,y:n.y,...(d.sizes.get(n.id)??{width:272,height:170})}),containerId=d.base.nodes.find(n=>n.id===d.id)?.container_id,rect=viewport.current?.getBoundingClientRect(),view=viewRef.current;
        const refs=painted.filter(n=>n.container_id===containerId&&!plan.moving.has(n.id)&&d.visible.has(n.id)).map(size).filter(n=>!rect||(n.x+n.width> -view.x/view.z-24/view.z&&n.x<(rect.width-view.x)/view.z+24/view.z&&n.y+n.height> -view.y/view.z-24/view.z&&n.y<(rect.height-view.y)/view.z+24/view.z));
        const feedback=smartGuides(moved.filter(n=>plan.moving.has(n.id)&&d.visible.has(n.id)).map(size),refs,view.z,d.snapLocks);
        d.snapLocks=feedback.locks;plan.alignment=feedback;if(feedback.gaps.length)plan.message+=' · 等距吸附';else if(feedback.lines.length)plan.message+=' · 已对齐';
        moved=moved.map(n=>plan.moving.has(n.id)?{...n,x:n.x+feedback.dx,y:n.y+feedback.dy}:n);
      }else d.snapLocks=undefined;
      plan.nodes=moved;plan.changed=moved.filter(n=>plan.moving.has(n.id));plan.message+=' · Shift 暂时关闭吸附';
    }else d.snapLocks=undefined;
    return plan;
  };
  const updateGesture = (cursor: Point, time = performance.now()) => {
    const area = marqueeRef.current;
    if (area) {
      const end = point(cursor.x, cursor.y); const next = { ...area, end }; marqueeRef.current = next; setMarquee(next);
      const left = Math.min(end.x, area.start.x), top = Math.min(end.y, area.start.y), right = Math.max(end.x, area.start.x), bottom = Math.max(end.y, area.start.y);
      setMulti(new Set([...marqueeBase.current, ...nodes.filter(n => !hidden(n) && n.x < right && n.x + (viewport.current?.querySelector<HTMLElement>(`[data-node-id="${n.id}"]`)?.offsetWidth || 272) > left && n.y < bottom && n.y + (viewport.current?.querySelector<HTMLElement>(`[data-node-id="${n.id}"]`)?.offsetHeight || 170) > top).map(n => n.id)])); return;
    }
    const d = drag.current; if (d?.type !== 'node' || !d.moved) return;
    d.cursor = cursor; const world = point(cursor.x, cursor.y);
    d.plan = dragPlan(d, world);
    const bounds = viewport.current?.getBoundingClientRect(); const inside = bounds && cursor.x >= bounds.left && cursor.x <= bounds.right && cursor.y >= bounds.top && cursor.y <= bounds.bottom;
    const target = d.plan.valid && inside ? d.plan.target : null;
    if (target && foldsRef.current.has(target)) {
      if (d.hover?.id !== target) d.hover = { id: target, since: time };
      else if (time - d.hover.since >= 650) {
        const next = new Set(foldsRef.current); next.delete(target); presence.transition(canvasSnapshot(sceneRef.current.nodes, scene.relations, active?.id, foldsRef.current, viewport.current), canvasSnapshot(sceneRef.current.nodes, scene.relations, active?.id, next)); foldsRef.current = next; setFolded(next); d.hover = undefined; d.remeasure = true;
        const index = new Map(d.base.nodes.map(n => [n.id, n]));
        d.visible = new Set(d.base.nodes.filter(n => { if (n.container_id !== d.base.nodes.find(source => source.id === d.id)?.container_id) return false; let parent = n.parent_id; const seen = new Set<string>(); while (parent && !seen.has(parent)) { if (next.has(parent)) return false; seen.add(parent); parent = index.get(parent)?.parent_id ?? null; } return true; }).map(n => n.id));
      }
    } else d.hover = undefined;
    setBranchPreview(d.plan); setScene({ ...d.base, nodes: d.plan.placement==='free'?d.plan.nodes:d.base.nodes.map(n => d.plan!.moving.has(n.id) ? { ...n, x: n.x + world.x - d.origin.x + (d.offsets?.get(n.id)?.x ?? 0) - (d.offsets?.get(d.id)?.x ?? 0), y: n.y + world.y - d.origin.y + (d.offsets?.get(n.id)?.y ?? 0) - (d.offsets?.get(d.id)?.y ?? 0) } : n) });
  };
  const edgePan = useCanvasEdgePan({ viewport, identity: identity + ':' + active?.id, enabled: status === 'ready' && !quick && !draft && !editing && !fileObject && !memberPanel,
    active: () => !!marqueeRef.current || (drag.current?.type === 'node' && !!drag.current.moved),
    cancel: (restore = true) => { const d = drag.current; if (restore || (d?.type === 'node' && d.identity === identity)) cancelBranch(); else { drag.current = null; setBranchPreview(null); } drag.current = null; marqueeRef.current = null; setMarquee(null); },
    tick: (cursor, dx, dy, time) => { const d = drag.current; if (dx || dy) { updateView({ ...viewRef.current, x: viewRef.current.x + dx, y: viewRef.current.y + dy }); updateGesture(cursor, time); }
      else if (d?.type === 'node' && d.hover && time - d.hover.since >= 650) updateGesture(cursor, time); },
  });
  useLayoutEffect(() => {
    const d = drag.current; if (d?.type !== 'node' || !d.remeasure || !d.cursor) return;
    d.remeasure = false;
    for (const n of d.base.nodes) { const el = viewport.current?.querySelector<HTMLElement>(`[data-node-id="${n.id}"]`); if (el) d.sizes.set(n.id, { width: el.offsetWidth || 272, height: el.offsetHeight || 170 }); }
    updateGesture(d.cursor);
  });
  const pointerMove = (e: ReactPointerEvent) => {
    edgePan.move(e.pointerId, { x: e.clientX, y: e.clientY });
    if (marqueeRef.current) { updateGesture({ x: e.clientX, y: e.clientY }); return; }
    if (connection.move(e.pointerId, { x: e.clientX, y: e.clientY })) return;
    const d = drag.current; if (!d || (d.type === 'node' && d.pointer !== e.pointerId)) return;
    if (d.type === 'pan') { updateView({ ...viewRef.current, x: d.pan.x + e.clientX - d.start.x, y: d.pan.y + e.clientY - d.start.y }); return; }
    if (!d.moved && Math.abs(e.clientX - d.start.x) + Math.abs(e.clientY - d.start.y) <= 3) return;
    d.snapDisabled=e.shiftKey;d.moved = true; viewport.current?.setPointerCapture(e.pointerId); updateGesture({ x: e.clientX, y: e.clientY });
  };
  const pointerUp = (e: ReactPointerEvent) => {
    if (marqueeRef.current) { updateGesture({ x: e.clientX, y: e.clientY }); edgePan.stop(); marqueeRef.current = null; setMarquee(null); if (viewport.current?.hasPointerCapture(e.pointerId)) viewport.current.releasePointerCapture(e.pointerId); return; }
    if (drag.current?.type === 'node' && drag.current.pointer !== e.pointerId) return;
    const d = drag.current; edgePan.stop(); drag.current = null; if (d && viewport.current?.hasPointerCapture(e.pointerId)) viewport.current.releasePointerCapture(e.pointerId);
    if (d?.type === 'node') {
      setBranchPreview(null);
      const rect = viewport.current?.getBoundingClientRect();
      const inside = rect && e.clientX >= rect.left && e.clientX <= rect.right && e.clientY >= rect.top && e.clientY <= rect.bottom;
      if (!d.moved) setNavigationTick(t => t + 1);
      if (!d.moved || !d.plan || !d.plan.valid || !inside) { nodeMotion.animate(nodeMotion.project(sceneRef.current.nodes), d.base.nodes); setFolded(d.foldedBase); foldsRef.current = d.foldedBase; sceneRef.current = d.base; setScene(d.base); return; }
      const n = d.base.nodes.find(n => n.id === d.id)!; const owner = d.base.containers.find(c => c.id === n.container_id)!;
      const dropEpoch = browseEpoch.current;
      const restore = () => { nodeMotion.animate(nodeMotion.project(sceneRef.current.nodes), d.base.nodes); if (browseEpoch.current === dropEpoch) { setFolded(d.foldedBase); foldsRef.current = d.foldedBase; } sceneRef.current = d.base; setScene(d.base); };
      d.snapDisabled=e.shiftKey;const plan = dragPlan(d, point(e.clientX, e.clientY));
      if (!plan.valid) { restore(); return; }
      nodeMotion.animate(nodeMotion.project(sceneRef.current.nodes), plan.nodes);
      setScene({ ...d.base, nodes: plan.nodes });
      if (plan.placement === 'child' && plan.target) setFolded(old => { const next = new Set(old); next.delete(plan.target!); return next; });
      void act(d.sources.length > 1 ? 'move_branches' : 'move_branch', { ...(d.sources.length > 1 ? { container_id: owner.id, objects: d.base.nodes.filter(n => d.sources.includes(n.id)).map(n => ({ object_id: n.id, expected_revision: n.revision })) } : { object_id: n.id, expected_revision: n.revision }), expected_container_revision: owner.revision, target_id: plan.target, placement: plan.placement, nodes: plan.changed.map(n => ({ node_id: n.node_id, expected_revision: n.layout_revision, position: { x: n.x, y: n.y } })) }, restore);
      return;
    }
    const drop = connection.finish(e.pointerId, { x: e.clientX, y: e.clientY });
    if (drop) {
      const source = byId.get(drop.source); if (!source || !writable(source)) return;
      if (drop.reconnect && drop.target) {
        const { relation, endpoint, revision } = drop.reconnect;
        const sourceId = endpoint === 'source' ? drop.target : relation.source_object_id;
        const targetId = endpoint === 'target' ? drop.target : relation.target_object_id;
        void act('reconnect_relation', { container_id: relation.container_id, expected_container_revision: revision, relation_id: relation.id, source_object_id: sourceId, target_object_id: targetId,appearance:{text:'',bend:0,...relation.appearance,[endpoint+'_anchor']:drop.anchor} });
      }
      else if(!drop.target && (drop.relationType || drop.reconnect)){
        const epoch=browseEpoch.current,key=identity;
        void act('create_relation_topic',{container_id:source.container_id,expected_container_revision:sceneRef.current.containers.find(c=>c.id===source.container_id)!.revision,source_object_id:source.id,position:drop.position,relation_type:drop.relationType??drop.reconnect?.relation.relation_type??'related',...(drop.reconnect?{relation_id:drop.reconnect.relation.id,endpoint:drop.reconnect.endpoint}:{})}).then(ok=>{if(ok&&identityRef.current===key&&browseEpoch.current===epoch&&createdNode.current)rename(createdNode.current);});
      }
      else if (drop.target) void act('connect', { source_object_id: source.id, target_object_id: drop.target, relation_type: drop.relationType??'related' });
      else createFromLine(source, drop.position, drop.port,drop.manualLayout);
    }
  };
  useEffect(() => {
    const host = viewport.current; if (!host) return;
    const wheel = (event: WheelEvent) => { if ((event.target as HTMLElement).closest('input,textarea,select,[contenteditable]')) return;
      event.preventDefault(); if (drag.current || marqueeRef.current || connection.preview) return;
      browseEpoch.current++; suppressReveal.current = true; const box = host.getBoundingClientRect(); updateView(wheelView(viewRef.current, event, { x: event.clientX - box.left, y: event.clientY - box.top }, box.height));
    };
    host.addEventListener('wheel', wheel, { passive: false }); return () => host.removeEventListener('wheel', wheel);
  });
  const fitAll = () => {
    browseEpoch.current++; suppressReveal.current = false;
    const area = viewport.current; if (!area || quick || drag.current || marqueeRef.current || connection.preview) return;
    const visible=nodes.filter(n=>!hidden(n)),cards=visible.map(n=>{const el=area.querySelector<HTMLElement>(`[data-node-id="${n.id}"]`);return{x:n.x,y:n.y,width:el?.offsetWidth||272,height:el?.offsetHeight||170};});
    const sizes=contentSizes(visible);const frameCounts=new Map<string,number>();
    for(const n of visible)for(const group of [...boundaryEntries(n.canvas_data).map(b=>b.group),n.canvas_data?.summary]){if(!group)continue;const inset=group===n.canvas_data?.summary?0:(frameCounts.get(group.members[0])??0)*26;if(group!==n.canvas_data?.summary)frameCounts.set(group.members[0],(frameCounts.get(group.members[0])??0)+1);const bounds=groupBounds(visible,group.members,sizes);if(bounds){cards.push({x:bounds.x-24-inset,y:bounds.y-40-inset,width:bounds.right-bounds.x+100+inset*2,height:bounds.bottom-bounds.y+64+inset*2});}}
    const dimensions=canvasSafeArea(area);
    let next=fitSafeView(cards,dimensions);
    // Parallel lanes are screen-sized: converge using the candidate zoom, not the old view.
    for(let pass=0;next&&pass<12;pass++){
      const lines=scene.relations.flatMap(r=>{const a=visible.find(n=>n.id===r.source_object_id),b=visible.find(n=>n.id===r.target_object_id);if(!a||!b)return [];const curve=relationCurve({...a,...sizes.get(a.id)!},{...b,...sizes.get(b.id)!},relationLane(scene.relations,r,next!.z).offset+(r.appearance?.bend??0),1,r.appearance),bounds=curveBounds(curve),padding=8/next!.z;const boxes=[{x:bounds.x-padding,y:bounds.y-padding,width:bounds.width+padding*2,height:bounds.height+padding*2}];
      const label=area.querySelector<SVGTextElement>(`[data-relation-id="${r.id}"] .kc-relation-label`);if(label&&typeof label.getBBox==='function'){const text=label.getBBox();if(text.width&&text.height){const x=Number(label.getAttribute('x')),y=Number(label.getAttribute('y'));boxes.push({x:curve.middle.x+text.x-x,y:curve.middle.y-12+text.y-y,width:text.width,height:text.height});}}
      if(selectedRelation===r.id&&active?.role!=='viewer')for(const control of [curve.control1,curve.control2])boxes.push({x:control.x-padding,y:control.y-padding,width:padding*2,height:padding*2});
      return boxes;});
      const fitted=fitSafeView([...cards,...lines],dimensions);if(!fitted)break;const settled=Math.abs(fitted.z-next.z)<1e-7;next=fitted;if(settled)break;
    }
    if (next) { setPrevious(viewRef.current); animateView(next); }
  };
  const zoomBy = (factor: number) => { browseEpoch.current++; suppressReveal.current = false; const box = viewport.current?.getBoundingClientRect(); if (box) animateView(current => zoomAt(current, current.z * factor, { x: box.width / 2, y: box.height / 2 })); };
  const resetZoom = () => { browseEpoch.current++; suppressReveal.current = false; const box = viewport.current?.getBoundingClientRect(); if (box) animateView(current => zoomAt(current, 1, { x: box.width / 2, y: box.height / 2 })); };
  const path = (a: KnowledgeNode, b: KnowledgeNode) => { const sizes=contentSizes([a,b]);return manualTopicPath({...a,...sizes.get(a.id)!},{...b,...sizes.get(b.id)!},b.canvas_data?.manual_layout,branchDirection(nodes.some(n=>n.id===b.id)?nodes:[...nodes,b],b.id)); };
  const nav = (n: KnowledgeNode, depth = 0): React.ReactNode => <div key={n.id}><button className={'kc-tree-row ' + (chosen.has(n.id) ? 'active' : '')} style={{ paddingLeft: 12 + depth * 14 }} onClick={() => { setMulti(new Set()); const c = container(n.container_id); if (c) { setTab(c.kind); if (c.kind === 'project') setProject(c.id); } locate(n.id); }}>{summaryParent(nodes,n) ? '└ ' : '◇ '}{n.title}</button>{!query && !folded.has(n.id) && nodes.filter(c => summaryParent(nodes,c) === n.id).sort(siblingCompare).map(c => nav(c, depth + 1))}</div>;
  const selectContainer = (id: string) => { if (browseBlocked) return; browseEpoch.current++; suppressReveal.current = false; cancelView(); nodeMotion.cancel(); setProject(id); setQuery(''); };
  const inlineToolsStyle=()=>{
    if(!quick?.node)return undefined;
    const el=viewport.current?.querySelector<HTMLElement>(`[data-node-id="${quick.node.id}"]`),area=viewport.current?.getBoundingClientRect();
    const card={x:quick.position.x*pan.z+pan.x,y:quick.position.y*pan.z+pan.y,width:(el?.offsetWidth??80)*pan.z,height:(el?.offsetHeight??48)*pan.z};
    const size={width:titleToolsMeasure.size.width*pan.z,height:titleToolsMeasure.size.height*pan.z};
    const obstacles=nodes.filter(n=>n.id!==quick.node!.id&&!hidden(n)).map(n=>{const size=contentSizes([n]).get(n.id)!;return{x:n.x*pan.z+pan.x,y:n.y*pan.z+pan.y,width:size.width*pan.z,height:size.height*pan.z};});
    const node=quick.node,measured={width:card.width/pan.z,height:card.height/pan.z},ports=(['child',...(node.parent_id?['sibling']:[])] as ('child'|'sibling')[]).map(port=>{const p=edgePoint(measured,portDirection(nodes,node.id,port),port==='child'?24:16);return{x:card.x+p.x*pan.z,y:card.y+p.y*pan.z,radius:12*pan.z};});
    const position=toolsPosition(card,{width:area?.width??1000,height:area?.height??800,top:viewport.current?canvasSafeArea(viewport.current).y:100},ports,size,[...obstacles,...(viewport.current?canvasObstacles(viewport.current):[])]);
    return {left:(position.left-card.x)/pan.z,top:(position.top-card.y)/pan.z,maxWidth:Math.max(80,((area?.width??1000)-24)/pan.z)};
  };
  const renderQuickEditor=(inline:boolean)=>(quick && <div className={inline?"kc-inline-title":"kc-quick kc-node"} style={inline?undefined:{ left: quick.position.x, top: quick.position.y }} onPointerDown={e => e.stopPropagation()} onDoubleClick={e => e.stopPropagation()} onBlur={e=>{if(!e.currentTarget.contains(e.relatedTarget as Node)&&!cancelQuick.current&&!imeActive.current)void saveQuick();}}>
            <label htmlFor="kc-quick-title">{quick.wrap?'插入父主题':quick.node ? '修改标题' : '新主题'}</label>
            <RichTitleEditor toolsRef={inline?titleToolsMeasure.ref:undefined} toolsStyle={inline?inlineToolsStyle():undefined} key={`${quick.container}:${quick.position.x}:${quick.position.y}`} ref={mountQuickInput} id="kc-quick-title" label={quick.wrap?'父主题标题':quick.node?'节点标题':'新主题标题'} text={quick.title} format={quick.titleFormat} style={quickTitleStyle(quick)} onChange={(title,titleFormat)=>setQuick(q=>q?{...q,title,titleFormat}:q)}
              onCompositionStart={()=>{imeActive.current=true;}} onCompositionEnd={()=>{imeActive.current=false;imeCanvasNode.current=null;imeEndAt.current=Date.now();}}
              onKeyDown={e=>{e.stopPropagation();if(e.nativeEvent.isComposing||imeActive.current||e.keyCode===229||e.repeat||(e.key==='Enter'&&Date.now()-imeEndAt.current<30))return;
                if(e.key==='Escape'&&!pending){e.preventDefault();cancelQuick.current=true;setQuick(null);focusCanvas();}
                if(!e.ctrlKey&&!e.metaKey&&!e.altKey&&!e.shiftKey&&(e.key==='Enter'||e.key==='Tab')){e.preventDefault();void saveQuick((quick.node||quick.fromLine||quick.wrap)&&e.key==='Enter'?undefined:e.key==='Tab'?'child':'sibling');}
              }}/>
            <small>{pending ? '正在保存…' : quick.node||quick.wrap ? 'Enter 确认 · Esc 取消' : quick.fromLine ? 'Enter 完成 · Tab 继续延伸 · Esc 取消' : 'Enter 继续同级 · Tab 创建子级 · Esc 取消'}</small>
            {error && !inline && <p role="alert">{error}</p>}
          </div>);
  const toolbarKeys = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (['ArrowLeft','ArrowRight','Home','End'].includes(e.key)) {
      e.preventDefault();e.stopPropagation();const buttons=[...e.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')];const i=buttons.indexOf(document.activeElement as HTMLButtonElement);const next=e.key==='Home'?0:e.key==='End'?buttons.length-1:(i+(e.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;buttons[next]?.focus();
    } else if(e.key==='Escape'){e.stopPropagation();focusCanvas();}
  };
  const visibleStatus = loadedIdentity === identity ? status : 'loading';
  return <CanvasSurfaceMotion identity={identity} reduced={reducedMotion}><div className="kc-page" data-reduced-motion={reducedMotion} onCopy={e => { if (nativeEditing(e.target) || !chosen.size) return; const record = copySelection(); if (record) { e.preventDefault(); e.clipboardData.setData('application/x-everydayai-branches', JSON.stringify(record)); e.clipboardData.setData('text/plain', nodes.filter(n => branchIds().has(n.id)).map(n => n.title).join('\n')); } }} onCut={e => { if (nativeEditing(e.target) || historyBlocked) return; const record = copySelection('cut'); if (record) { e.preventDefault(); e.clipboardData.setData('application/x-everydayai-branches', JSON.stringify(record)); e.clipboardData.setData('text/plain', nodes.filter(n => branchIds().has(n.id)).map(n => n.title).join('\n')); } }} onPaste={pasteEvent} onKeyDown={onKey} onKeyUp={e => { if (e.code === 'Space') space.current = false;if(e.key==='Shift'&&drag.current?.type==='node'&&drag.current.moved){drag.current.snapDisabled=false;if(drag.current.cursor)updateGesture(drag.current.cursor);} }} onBlur={() => { space.current = false; }}>
    <header className="kc-header"><Link to="/chat">← 主对话</Link><strong>知识画布</strong><span>V3 · 主对话知识引用</span>{clipboard?.mode === 'cut' && <small role="status">已剪切 {clipboard.objects.length} 个节点，选择位置粘贴 · Esc 取消</small>}<small aria-live="polite">{chosen.size ? `已选 ${chosen.size} 个节点（含后代 ${branchIds().size} 个）` : ''}</small><details id="kc-history-help" className="kc-history-help"><summary title="撤销重做范围与限制"><CircleHelp size={18} aria-hidden="true"/><span className="kc-sr-only">撤销重做范围与限制</span></summary><div className="kc-history-content"><p>撤销：{historyReason('undo') || '可撤销当前账号在当前企业或个人范围的上一步画布操作'}。重做：{historyReason('redo') || '可恢复刚撤销的画布操作'}。</p><p>保留最近 100 步：创建、改名、拉线、关联线删除与重连、层级调整、排序、移动、粘贴和删除分支，以及全图配色/背景/字体/层级样式、标题换行、节点宽度、保留子主题删除、导图模板、节点视频/网址、概要/外框范围、节点样式、节点图片和关联线文字/弯曲。正文保存、文件更新和成员权限不进入画布撤销；它们可能使该空间旧操作失效。撤销后进行新的修改会清空重做。其他人更新知识或权限变化时不会覆盖新内容；输入框内 ⌘Z / Ctrl+Z 只撤销文字编辑。</p><p>Mac：⌘Z 撤销、⇧⌘Z 重做；Windows：Ctrl+Z 撤销、Ctrl+Y 或 Ctrl+Shift+Z 重做。</p></div></details><button disabled={pending || status !== 'ready'} onClick={() => void checkSave()} title="刷新"> <RefreshCw aria-hidden="true"/><span className="kc-sr-only">刷新</span></button><span role="status">{pending ? '正在保存…' : status === 'ready' ? '已连接' : ''}</span></header>
    {!sourceClosed && status==='ready' && sourceId && sourceRevision>0 && <KnowledgeSource key={identity+sourceId+sourceRevision+sourceQuery.get('binding')} objectId={sourceId} revision={sourceRevision} bindingId={sourceQuery.get('binding')} generation={sourceQuery.get('generation')} onClose={()=>{ setSourceClosed(true); focusCanvas(); }} />}

    {visibleStatus !== 'ready' && error && <div className="kc-error" role="alert">{error}</div>}
    {visibleStatus !== 'ready' ? <div className="kc-state">{visibleStatus === 'loading' ? '正在加载知识画布…' : status === 'unavailable' ? '知识画布尚未向当前账号开放' : '加载失败，请重新打开页面重试'}</div> : <main className="kc-main">
      <div className="kc-workspace" style={{backgroundColor:active?.canvas_theme?.background&&active.canvas_theme.background!=='auto'?active.canvas_theme.background:undefined,backgroundImage:active?.canvas_theme?.grid===false?'none':undefined}} ref={viewport} tabIndex={0} aria-label="知识画布" onCompositionStart={e=>{if(e.target!==e.currentTarget||historyBlocked||chosen.size!==1||selectedRelation)return;const n=byId.get([...chosen][0]);if(n&&writable(n)){imeCanvasNode.current=n.id;flushSync(()=>rename(n,''));}}} onCompositionEnd={e=>{if(e.target===e.currentTarget){const id=imeCanvasNode.current;imeCanvasNode.current=null;if(e.data&&id)setQuick(q=>q?.node?.id===id?{...q,title:e.data}:q);}}} onPointerDown={e => pointerDown(e)} onPointerMove={pointerMove} onPointerUp={pointerUp} onLostPointerCapture={() => { edgePan.stop(); marqueeRef.current = null; setMarquee(null); connection.cancel(); cancelBranch(); }} onPointerCancel={() => { edgePan.stop(); marqueeRef.current = null; setMarquee(null); connection.cancel(); cancelBranch(); drag.current = null; void refresh().catch(e => setError(message(e))); }}
        onDoubleClick={e => { if (!(e.target as HTMLElement).closest('[data-node-id],[data-relation-id],button')) { if (!active || active.role === 'viewer' || quick || busy.current) return;
          const position = freePosition(point(e.clientX, e.clientY)); cancelQuick.current = false; setError(''); setMenu(null);
          setQuick({ node: null, container: active.id, parent: null, position, title: '' }); revealQuick(position); } }}
        onContextMenu={e => { if ((e.target as Element).closest('input,textarea,[contenteditable]')) return; e.preventDefault(); if(connection.preview){connection.cancel();return;} if (browseBlocked || drag.current || marquee) return; connection.cancel(); setSelectedRelation(null); const n = (e.target as HTMLElement).closest<HTMLElement>('[data-node-id]')?.dataset.nodeId ?? null; if (n && !chosen.has(n)) { setMulti(new Set()); setSelected(n); } setMenu({ x: e.clientX, y: e.clientY, point: point(e.clientX, e.clientY), parent: n }); }}
>
        <div className="kc-feedback" onPointerDown={e => e.stopPropagation()} onDoubleClick={e => e.stopPropagation()} onContextMenu={e => e.stopPropagation()}>
    {currentRelation && <div className="kc-relation-toolbar" role="group" aria-label="选中关联线操作"><span role="status">已选关联线（{relationTypes[currentRelation.relation_type] ?? '关联'}）：{byId.get(currentRelation.source_object_id)?.title} → {byId.get(currentRelation.target_object_id)?.title} · {active?.role === 'viewer' ? '只读' : '拖动圆形端点重新连接 · Delete / Backspace 删除 · Esc 取消选择'}</span><button disabled={historyBlocked || active?.role === 'viewer'} onClick={() => void deleteRelation()}>删除关联线</button><button disabled={!!connection.preview||!!relationText} onClick={() => { setSelectedRelation(null); focusCanvas(); }}>取消选线</button></div>}
    {historyNotice && <small className="kc-operation-note" role="status">{historyNotice}</small>}
    {copyPending && <small className="kc-operation-note" role="status">正在固定复制内容快照…</small>}
    {copyNotice && clipboard?.mode === 'copy' && !copyPending && <small className="kc-operation-note" role="status">已复制 {clipboard.objects.length} 个节点的内容快照；含正文与固定附件版本，有效期 7 天，粘贴时仍检查当前权限。</small>}
    {error && <div className="kc-error" role="alert">{error}<button type="button" aria-label="关闭画布错误提示" onClick={() => { setError(''); focusCanvas(); }}>关闭</button></div>}
        </div>
        <div className="kc-canvas-tools" role="toolbar" aria-label="画布操作" onPointerDown={e=>e.stopPropagation()} onDoubleClick={e=>e.stopPropagation()} onKeyDown={toolbarKeys}><button aria-label="全图样式" title="全图样式 · 配色、背景、字体与层级样式" disabled={historyBlocked||!active||active.role==='viewer'} onClick={()=>{if(active)setMapEditor({container:active.id,revision:active.revision,theme:normalizeMapTheme(active.canvas_theme),reset:false});}}><Palette aria-hidden="true"/></button><button aria-label="回到中心主题" title="回到中心主题 · Home（Mac Fn+←）；保持当前缩放" disabled={browseBlocked||!centerTopic(nodes,navigationId,active?.id)} onClick={goToCenter}><LocateFixed aria-hidden="true"/></button><button aria-label="折叠全部后代" title="折叠所选分支的全部后代；未选中时折叠当前空间 · Alt/Option+Shift+↑" disabled={browseBlocked||!nodes.some(n=>n.parent_id)} onClick={()=>foldAll(false)}><FoldVertical aria-hidden="true"/></button><button aria-label="展开全部后代" title="展开所选分支的全部后代；未选中时展开当前空间 · Alt/Option+Shift+↓" disabled={browseBlocked||!nodes.some(n=>n.parent_id)} onClick={()=>foldAll(true)}><UnfoldVertical aria-hidden="true"/></button><button aria-label="关联线" title="关联线 · 单选后点击目标或空白处；双选直接连接" disabled={historyBlocked || ![1,2].includes(toolNodes.length) || active?.role === 'viewer'} onClick={()=>connectSelected()}><GitBranch aria-hidden="true"/></button><button aria-label="节点视频" title="上传或选择节点视频" disabled={historyBlocked||toolNodes.length!==1||active?.role==='viewer'} onClick={()=>openMedia('video')}><Video aria-hidden="true"/></button><button aria-label="网页与视频链接" title="网页与视频链接" disabled={historyBlocked||toolNodes.length!==1||active?.role==='viewer'} onClick={()=>openMedia('link')}><Link2 aria-hidden="true"/></button><button aria-label="样式" title="样式 · 批量设置所选节点" disabled={historyBlocked||!toolNodes.length||active?.role==='viewer'} onClick={()=>openVisual('style')}><Palette aria-hidden="true"/></button><button aria-label="格式刷" title="格式刷 · 先选来源，再点击目标节点；Esc 取消" aria-pressed={brush} disabled={historyBlocked||toolNodes.length!==1||active?.role==='viewer'} onClick={()=>{setStyleClipboard(styleSnapshot(mapAppearance(toolNodes[0],nodes,active?.canvas_theme)));setBrush(true);focusCanvas();}}><Paintbrush aria-hidden="true"/></button><button aria-label="概要" title="概要 · 大括号汇总选中分支" disabled={historyBlocked || !chosen.size || active?.role === 'viewer'} onClick={()=>void addGroup('summary')}><Braces aria-hidden="true"/></button><button aria-label="外框" title="外框 · 包围选中分支" disabled={historyBlocked || !chosen.size || active?.role === 'viewer'} onClick={()=>void addGroup('boundary')}><SquareDashed aria-hidden="true"/></button><button aria-label="任务" title="任务" disabled={historyBlocked || toolNodes.length !== 1} onClick={()=>openAnnotation('task')}><ListTodo aria-hidden="true"/></button><ToolMenu.Root modal={false}><ToolMenu.Trigger asChild><button aria-label="插入" title="插入" disabled={historyBlocked || active?.role === 'viewer'}><Plus aria-hidden="true"/></button></ToolMenu.Trigger><ToolMenu.Portal><ToolMenu.Content className="kc-tools-menu" collisionPadding={8} onCloseAutoFocus={e=>{e.preventDefault();if(quickInput.current?.isConnected)quickInput.current.focus({preventScroll:true});else focusCanvas();}}><ToolMenu.Item disabled={toolNodes.length!==1 || !writable(toolNodes[0])} onSelect={()=>startQuick(toolNodes[0],true)}>子主题</ToolMenu.Item><ToolMenu.Item disabled={toolNodes.length!==1 || !writable(toolNodes[0])} onSelect={()=>startQuick(toolNodes[0],false)}>同级主题</ToolMenu.Item><ToolMenu.Item disabled={toolNodes.length!==1||!writable(toolNodes[0])} onSelect={()=>startParent(toolNodes[0])}>父主题 · ⌘Enter / Ctrl+Enter</ToolMenu.Item><ToolMenu.Item onSelect={()=>openCreate(point((viewport.current?.getBoundingClientRect().left??0)+100,(viewport.current?.getBoundingClientRect().top??0)+100),null)}>自由主题</ToolMenu.Item><ToolMenu.Item disabled={!toolNodes.length} onSelect={()=>openVisual('style')}>样式（应用到所选节点）</ToolMenu.Item>{(['left','center','right'] as const).map((align,i)=><ToolMenu.Item key={align} disabled={!toolNodes.length} onSelect={()=>void saveAnnotations(toolNodes.map(node=>({node,data:{...node.canvas_data,title_format:{runs:node.canvas_data?.title_format?.runs??[],align}}})))}>{['左对齐所选主题','居中所选主题','右对齐所选主题'][i]}</ToolMenu.Item>)}<ToolMenu.Item disabled={!toolNodes.length} onSelect={()=>void saveAnnotations(toolNodes.map(node=>({node,data:editTopicStyle(node.title,node.canvas_data,null,defaultStyle)})))}>恢复所选主题默认样式与文字格式</ToolMenu.Item><ToolMenu.Item disabled={toolNodes.length!==1} onSelect={()=>setStyleClipboard(styleSnapshot(mapAppearance(toolNodes[0],nodes,active?.canvas_theme)))}>复制样式</ToolMenu.Item><ToolMenu.Item disabled={!toolNodes.length || styleClipboard===undefined} onSelect={()=>void saveAnnotations(toolNodes.map(node=>({node,data:pasteTopicStyle(node.title,node.canvas_data,styleClipboard!)})))}>粘贴样式</ToolMenu.Item><ToolMenu.Item disabled={toolNodes.length!==1||!toolNodes[0]?.canvas_data?.width} onSelect={()=>void setTopicWidth(toolNodes[0],null)}>恢复默认宽度</ToolMenu.Item><ToolMenu.Item disabled={toolNodes.length!==1} onSelect={()=>openVisual('image')}>节点内图片</ToolMenu.Item><ToolMenu.Item disabled={toolNodes.length!==1 || !toolNodes[0]?.canvas_data?.summary} onSelect={()=>openVisual('summary')}>编辑概要范围</ToolMenu.Item><ToolMenu.Item disabled={toolNodes.length!==1 || !boundaryEntries(toolNodes[0]?.canvas_data).length} onSelect={()=>openVisual('boundary',toolNodes[0],boundaryEntries(toolNodes[0]?.canvas_data)[0]?.key)}>编辑外框范围</ToolMenu.Item><ToolMenu.Item disabled={![1,2].includes(toolNodes.length)} onSelect={()=>connectSelected('dependency')}>依赖关联线</ToolMenu.Item><ToolMenu.Item disabled={toolNodes.length!==1} onSelect={()=>{ const n=toolNodes[0],key=identity;void knowledgeApi.object(n.id).then(object=>{if(identityRef.current===key){setEditing(null);setSelected(n.id);setMulti(new Set());setFileObject(object);}}).catch(e=>{if(identityRef.current===key)setError(message(e));}); }}>文件资料</ToolMenu.Item></ToolMenu.Content></ToolMenu.Portal></ToolMenu.Root><span className="kc-tool-divider"/><div className="kc-undo-controls">
      <button disabled={historyBlocked || !history.undo?.can_apply} aria-describedby="kc-history-help" title={historyReason('undo') || '撤销 · ⌘Z / Ctrl+Z'} onClick={() => void changeHistory('undo')}><Undo2 aria-hidden="true"/><span className="kc-sr-only">撤销{history.undo ? ' ' + ((history.undo.container_id && history.undo.container_id !== active?.id ? '「' + (history.undo.container_title ?? '其他空间') + '」' : '') + (historyLabels[history.undo.operation] ?? '操作')) : ''}</span></button>
      <button disabled={historyBlocked || !history.redo?.can_apply} aria-describedby="kc-history-help" title={historyReason('redo') || '重做 · ⇧⌘Z / Ctrl+Y / Ctrl+Shift+Z'} onClick={() => void changeHistory('redo')}><Redo2 aria-hidden="true"/><span className="kc-sr-only">重做{history.redo ?  ' ' + ((history.redo.container_id && history.redo.container_id !== active?.id ? '「' + (history.redo.container_title ?? '其他空间') + '」' : '') + (historyLabels[history.redo.operation] ?? '操作')) : ''}</span></button>
      {(historyUnavailable || (history.undo && !history.undo.can_apply) || (history.redo && !history.redo.can_apply)) && <small role="status">{historyUnavailable ? '撤销记录暂不可用，请刷新' : '知识或权限已更新，旧操作无法恢复'}</small>}
    </div><button disabled={historyBlocked || !chosen.size} onClick={() => copyToSystem()} title="复制分支"> <Copy aria-hidden="true"/><span className="kc-sr-only">复制分支</span></button><button disabled={historyBlocked || !chosen.size} onClick={() => copyToSystem('cut')} title="剪切分支"> <Scissors aria-hidden="true"/><span className="kc-sr-only">剪切分支</span></button><button disabled={historyBlocked || !navigationId || !nodes.some(n => n.parent_id === navigationId)} title="折叠/展开 · ⌘/ / Ctrl+/" aria-expanded={navigationId ? !folded.has(navigationId) : undefined} onClick={() => { if (navigationId) toggleFold(navigationId); }}><ChevronsLeftRight aria-hidden="true"/><span className="kc-sr-only">{navigationId && folded.has(navigationId) ? '展开分支' : '折叠分支'}</span></button><button disabled={historyBlocked || !clipboard} onClick={() => void pasteSelection()} title="粘贴分支"> <ClipboardPaste aria-hidden="true"/><span className="kc-sr-only">粘贴分支</span></button><button disabled={historyBlocked || !chosen.size} onClick={() => void deleteSelection()} title="删除分支"> <Trash2 aria-hidden="true"/><span className="kc-sr-only">删除分支</span></button></div>
        {toolNodes.length === 1 && !quick && !draft && !editing && !mediaEditor && !mediaView && !visual && !annotation && !branchPreview && !connection.preview && (()=>{const n=drawnById.get(toolNodes[0].id)??toolNodes[0];const rect=viewport.current?.getBoundingClientRect();const size=contentSizes([n]).get(n.id)!,card={x:n.x*pan.z+pan.x,y:n.y*pan.z+pan.y,width:size.width*pan.z,height:size.height*pan.z},ports=[{...edgePoint(size,portDirection(nodes,n.id,'child')),radius:8},{...edgePoint(size,portDirection(nodes,n.id,'child'),24),radius:12},...(n.parent_id?[{...edgePoint(size,portDirection(nodes,n.id,'sibling'),16),radius:12}]:[])].map(p=>({x:card.x+p.x*pan.z,y:card.y+p.y*pan.z,radius:p.radius*pan.z})),position=toolsPosition(card,{width:rect?.width??1000,height:rect?.height??800,top:viewport.current?canvasSafeArea(viewport.current).y:100},ports,nodeToolsMeasure.size,nodes.filter(other=>other.id!==n.id&&!hidden(other)).map(other=>{const size=contentSizes([other]).get(other.id)!;return{x:other.x*pan.z+pan.x,y:other.y*pan.z+pan.y,width:size.width*pan.z,height:size.height*pan.z};}).concat(viewport.current?canvasObstacles(viewport.current):[]));return <div ref={nodeToolsMeasure.ref} className="kc-node-tools" role="toolbar" onKeyDown={toolbarKeys} aria-label={'节点操作 '+n.title} style={{...position,maxWidth:Math.max(80,(rect?.width??1000)-24)}} onPointerDown={e=>e.stopPropagation()} onDoubleClick={e=>e.stopPropagation()}>
          <button title="节点图片" aria-label="节点图片" disabled={historyBlocked || !writable(n)} onClick={()=>openVisual('image',n)}><FileImage size={17}/></button><button title="任务" aria-label="节点任务" disabled={pending} onClick={()=>openAnnotation('task',n)}><ListTodo size={17}/></button><button title="概要" aria-label="节点概要" disabled={historyBlocked||!writable(n)} onClick={()=>void addGroup('summary')}><Braces size={17}/></button><button title="外框" aria-label="节点外框" disabled={historyBlocked||!writable(n)} onClick={()=>void addGroup('boundary')}><SquareDashed size={17}/></button>
        </div>;})()}
        <div className="kc-location"><span>{active?.title ?? scopes[tab]}</span>{previous && <button onClick={() => { animateView(previous); setPrevious(null); }}>返回视野</button>}</div>
        <div className="kc-zoom"><button aria-label="缩小" disabled={!!quick || !!branchPreview || !!marquee || !!connection.preview} onClick={() => zoomBy(1 / 1.2)}>−</button><button aria-label="回到100%" disabled={!!quick || !!branchPreview || !!marquee || !!connection.preview} onClick={resetZoom}>{Number((pan.z * 100).toPrecision(3))}%</button><button disabled={!!quick || !!branchPreview || !!marquee || !!connection.preview || !nodes.length} onClick={fitAll}>适配全图</button><button aria-label="放大" disabled={!!quick || !!branchPreview || !!marquee || !!connection.preview} onClick={() => zoomBy(1.2)}>＋</button></div>
        <div className="kc-world" style={{ transform: `translate(${pan.x}px,${pan.y}px) scale(${pan.z})` }}>
          <svg className="kc-edges" aria-hidden="true">{drawnNodes.filter(n => n.parent_id && !n.canvas_data?.summary && !hidden(n)).map(n => { const p = drawnById.get(n.parent_id!); return p && <path key={n.id} className={presence.entering.has(n.id) ? 'kc-edge-enter' : undefined} d={path(p, n)} style={{stroke:mapBranchColor(nodes,n,active?.canvas_theme)}} />; })}</svg>
          {!!presence.leaving.length && <svg className="kc-edges kc-departing-edges" aria-hidden="true">{presence.leaving.filter(n => n.parent_id&&!n.canvas_data?.summary).map(n => { const parent = presence.snapshot.nodes.find(p => p.id === n.parent_id); return parent && <path key={n.id} d={path(parent, n)} />; })}</svg>}
          {!!presence.snapshot.relations.length && <div inert aria-hidden="true" className="kc-departing-edges"><CanvasRelations nodes={presence.snapshot.nodes} relations={presence.snapshot.relations} measurements={presence.snapshot.sizes} viewport={viewport} selected={null} writable={false} disabled zoom={pan.z} dragging={null} onSelect={() => {}} onReconnect={() => {}} /></div>}
          <CanvasGroupOverlays selected={groupSelection} disabled={browseBlocked} onSelect={selectGroup} onContext={(group,position)=>setMenu({...position,point:point(position.x,position.y),parent:null,group})} zoom={pan.z} onRange={!historyBlocked&&active?.role!=='viewer'?async(node,kind,members,key)=>{const epoch=browseEpoch.current;const data=kind==='boundary'?setBoundary(node.canvas_data,key??'legacy',{...readGroup(node,{node:node.id,kind,key})!,members}):{...node.canvas_data,summary:{...node.canvas_data!.summary!,members}};const ok=await saveAnnotations([{node,data}]);if(browseEpoch.current===epoch)focusCanvas();return ok;}:undefined} nodes={drawnNodes.filter(n=>liveVisibleIds.has(n.id))} sizes={contentSizes(nodes)} onSummary={n=>{setGroupSelection(null);rename(n);}} onBoundary={(n,key)=>openVisual('boundary',n,key)}/><CanvasRelations labelDraft={relationText} onLabelDraft={relationLabelDraft} labelWritable={active?.role!=='viewer'&&!pending&&!unconfirmed.current&&status==='ready'} nodes={visibleNodes} relations={visibleRelations} viewport={viewport} selected={selectedRelation} onAppearance={async(r,appearance)=>{if(!active)return false;return act('update_relation',{container_id:active.id,expected_container_revision:relationText?.relation.id===r.id?relationText.containerRevision??active.revision:active.revision,relation_id:r.id,appearance});}} writable={active?.role !== 'viewer' && !historyBlocked} disabled={browseBlocked} zoom={pan.z} dragging={connection.preview?.reconnect?.relation.id ?? null} entering={presence.entering} onSelect={selectRelation} onReconnect={(relation, endpoint, e) => {
            if (!active || active.role === 'viewer' || historyBlocked) return;
            cancelView(); connection.beginReconnect(relation, endpoint, active.revision, e.pointerId, { x: e.clientX, y: e.clientY });
          }} />
          {[...nodes.filter(n => !hidden(n)), ...presence.leaving.filter(n => !liveVisibleIds.has(n.id))].map(n => <article key={n.id} inert={departingIds.has(n.id) || undefined} aria-hidden={departingIds.has(n.id) || undefined} data-node-id={departingIds.has(n.id) ? undefined : n.id} data-departure-id={departingIds.has(n.id) ? n.id : undefined} aria-label={departingIds.has(n.id) ? undefined : n.title} className={'kc-node ' + (presence.entering.has(n.id) ? 'kc-node-enter ' : '') + (presence.emphasis.has(n.id) ? 'kc-node-emphasis ' : '') + (departingIds.has(n.id) ? 'kc-node-exit ' : '') + (chosen.has(n.id) ? 'selected' : '') + (clipboard?.mode === 'cut' && clipboard.objects.some(o => o.object_id === n.id) ? ' kc-cut-source' : '') + (branchPreview?.moving.has(n.id) ? ' kc-branch-moving' : '') + (branchPreview?.target === n.id ? branchPreview.valid ? ' kc-drop-target' : ' kc-drop-invalid' : '') + (connection.preview?.target === n.id ? connection.preview.valid ? ' kc-drop-target' : ' kc-drop-invalid' : '') + (connection.preview?.source === n.id ? ' kc-connect-source' : '')} style={{ ...mapNodeStyle(quick?.node?.id===n.id?{...n,title:quick.title,canvas_data:{...n.canvas_data,title_format:quick.titleFormat}}:n,quick?.node?nodes.map(v=>v.id===quick.node!.id?{...v,title:quick.title,canvas_data:{...v.canvas_data,title_format:quick.titleFormat}}:v):nodes,active?.canvas_theme), left: drawnById.get(n.id)?.x ?? n.x, top: drawnById.get(n.id)?.y ?? n.y, '--kc-presence-opacity': presence.starts[n.id]?.opacity ?? (departingIds.has(n.id) ? 1 : 0), '--kc-presence-scale': presence.starts[n.id]?.scale ?? (departingIds.has(n.id) ? 1 : .97) } as CSSProperties} onDragOver={e=>{if(!historyBlocked&&writable(n)&&e.dataTransfer.types.includes('Files')){e.preventDefault();e.stopPropagation();e.dataTransfer.dropEffect='copy';}}} onDrop={e=>{e.preventDefault();e.stopPropagation();const file=e.dataTransfer.files[0];if(file)void uploadNodeImage(n,file);}} onPointerDown={e => { e.stopPropagation(); pointerDown(e, n); }} onDoubleClick={e => { e.stopPropagation(); if (!(e.target as HTMLElement).closest('button,input')) { if (writable(n)) rename(n,undefined,titleCaretAt((e.target as HTMLElement).closest('h3'),e.clientX,e.clientY)); else void openEditor(n); } }}>
            {n.kind !== 'topic' && <small>{kinds[n.kind]}</small>}<h3 style={{fontWeight:400,...titleStyle(mapAppearance(n,nodes,active?.canvas_theme))}}>{quick?.node?.id===n.id&&!quick.standalone?renderQuickEditor(true):<RichTitle text={n.title} format={n.canvas_data?.title_format}/>}</h3>{n.canvas_data?.image&&<CanvasNodeImage node={n} zoom={pan.z} disabled={historyBlocked||!writable(n)||!chosen.has(n.id)} onResize={width=>{if(n.canvas_data?.image)void saveAnnotations([{node:n,data:{...n.canvas_data,image:{...n.canvas_data.image,width}}}]);}} onRemove={writable(n)&&!historyBlocked?()=>void saveAnnotations([{node:n,data:{...n.canvas_data,image:null}}]):undefined} onPreview={src=>{if(!browseBlocked)setImageView({identity,id:n.id,src,key:n.canvas_data!.image!.binding_id+':'+n.canvas_data!.image!.revision,title:n.title});}}/>}<CanvasMediaCard node={n} writable={chosen.has(n.id)&&writable(n)&&!historyBlocked} onView={source=>{if(!browseBlocked)setMediaView({node:n,source});}} onEdit={mode=>openMedia(mode,n)}/><AttachmentSummary node={n} onRemove={writable(n)&&!historyBlocked?file=>void openFileAction(n,{kind:'remove',binding:file}):undefined} onMore={()=>void openFiles(n)} onPreview={version=>{if(!pending&&!quick&&!editing&&!unconfirmed.current){setSelected(n.id);setMulti(new Set());setFilePreview({id:n.id,version,identity});}}}/><div className="kc-card-actions" onPointerDown={e=>e.stopPropagation()} onDoubleClick={e=>e.stopPropagation()}><button title={writable(n)?"目录资料":"展开查看"} aria-label={writable(n)?"目录资料":"展开查看"} disabled={pending||!!quick} onClick={()=>void openEditor(n)}><FileText size={14} aria-hidden="true" focusable="false"/>{writable(n)?"目录资料":"展开查看"}</button><NodeFileMenu onLabels={()=>openAnnotation('labels',n)} node={n} canWrite={writable(n)} disabled={filePending||pending||!!unconfirmed.current||!!quick||!!editing} onMore={()=>void openFiles(n)} onAction={action=>void openFileAction(n,action)}/></div><div className="kc-node-annotations">{n.canvas_data?.note && <button aria-label={'查看备注 '+n.title} title={n.canvas_data.note.slice(0,200)} onClick={()=>openAnnotation('note',n)}><StickyNote size={14}/></button>}{n.canvas_data?.labels?.slice(0,2).map(tag=><button title={tag} key={tag} onClick={()=>openAnnotation('labels',n)}>{tag}</button>)}{(n.canvas_data?.labels?.length ?? 0)>2 && <button aria-label={'查看全部标签 '+n.title} onClick={()=>openAnnotation('labels',n)}>+{n.canvas_data!.labels!.length-2}</button>}{n.canvas_data?.task && <button aria-label={'查看任务 '+n.title} onClick={()=>openAnnotation('task',n)}>{n.canvas_data.task.progress===100?'☑':'☐'} {n.canvas_data.task.progress}%{n.canvas_data.task.priority?' · P'+n.canvas_data.task.priority:''}{n.canvas_data.task.due?' · '+n.canvas_data.task.due:''}</button>}</div>
            {writable(n) && chosen.size===1 && chosen.has(n.id) && !quick && !annotation && !draft && !editing && !branchPreview && <>{(['child',...(n.parent_id?['sibling']:[])] as ('child'|'sibling')[]).map(port=><button key={port} data-creation-port={port} style={portStyle(nodes,n.id,port,12,port==='child'?24:16)} className={'kc-create-plus kc-create-'+port} aria-label={'为 '+n.title+' 创建'+(port==='child'?'子级':'同级')} title={port==='child'?'创建子主题 · 点击自动排布，拖动指定位置':'创建同级主题 · 点击自动排布，拖动指定位置'} disabled={historyBlocked} onClick={e=>{if(e.detail===0)startQuick(n,port==='child');}} onPointerDown={e=>{e.stopPropagation();e.preventDefault();if(e.button!==0||historyBlocked)return;cancelView();connection.begin(n.id,e.pointerId,{x:e.clientX,y:e.clientY},port);}}>＋</button>)}</>}
            {writable(n)&&chosen.size===1&&chosen.has(n.id)&&!quick&&!historyBlocked&&<TopicWidthHandle width={contentSizes([n]).get(n.id)!.width} zoom={pan.z} height={contentSizes([n]).get(n.id)!.height} onPreview={()=>{}} onCommit={()=>{}} onResizePreview={size=>{cancelView();nodeMotion.cancel();const base=sceneRef.current.nodes.find(v=>v.id===n.id)!;setWidthPreview({id:n.id,...size,x:base.x+size.dx,y:base.y+size.dy});}} onCancel={()=>setWidthPreview(null)} onResizeCommit={size=>void setTopicSize(n,size)}/>}
            {(writable(n) || nodes.some(c => c.parent_id === n.id)) && <button className="kc-handle" style={foldStyle(nodes,n.id)} aria-label={nodes.some(c => c.parent_id === n.id) ? '折叠或展开 ' + n.title : '从 ' + n.title + ' 拉线创建'} aria-expanded={nodes.some(c => c.parent_id === n.id) ? !folded.has(n.id) : undefined} title={writable(n) ? '单击折叠/展开分支，拖动创建子主题' : '单击折叠/展开分支'} onClick={e => { if ((e.detail === 0 || !writable(n) || pending || !!unconfirmed.current) && nodes.some(c => c.parent_id === n.id)) toggleFold(n.id); }} onPointerDown={e => { e.stopPropagation(); e.preventDefault(); if (e.button !== 0 || !writable(n) || busy.current || (quick && (quick.node || quick.title.trim()))) return; if (quick) { void saveQuick(); return; } cancelView(); setSelected(n.id); setMenu(null); connection.begin(n.id, e.pointerId, { x: e.clientX, y: e.clientY }); }} />}
          </article>)}
          {marquee && <div className="kc-marquee" style={{ left: Math.min(marquee.start.x, marquee.end.x), top: Math.min(marquee.start.y, marquee.end.y), width: Math.abs(marquee.end.x - marquee.start.x), height: Math.abs(marquee.end.y - marquee.start.y) }} />}
          {branchPreview?.alignment&&<SmartGuideOverlay feedback={branchPreview.alignment} zoom={pan.z}/>}
          {branchPreview?.valid && branchPreview.placement !== 'free' && drag.current?.type === 'node' && drag.current.sources.map(id => { const slot = branchPreview.nodes.find(n => n.id === id); const size = drag.current?.type === 'node' ? drag.current.sizes.get(id) : undefined; return slot && <div key={id} className="kc-drop-slot" aria-label="分支落点预览" style={{ left: slot.x, top: slot.y, width: size?.width ?? 272, height: size?.height ?? 170 }} />; })}
          {branchPreview?.marker && <div className={'kc-insert-marker ' + (branchPreview.valid ? '' : 'invalid')+(branchPreview.marker.height?' vertical':'')} style={{ left: branchPreview.marker.x, top: branchPreview.marker.y, width: branchPreview.marker.width,height:branchPreview.marker.height??4 }} />}
          {editing&&byId.has(editing.id)&&(()=>{const n=drawnById.get(editing.id)??byId.get(editing.id)!;const size=contentSizes([n]).get(n.id)!;return <InlineBodyEditor key={identity+editing.id} zoom={pan.z} object={editing} base={editingBase.current} pending={pending} error={error} unknown={!!unconfirmed.current} style={{left:n.x,top:n.y+size.height+12,width:Math.max(300,size.width)}} onChange={body=>setEditing(current=>current?{...current,body}:current)} onReveal={()=>revealQuick({x:n.x,y:n.y+size.height+12},{width:Math.max(300,size.width),height:240})} onSave={saveBody} onCheck={()=>void checkSave()} onClose={()=>{setEditing(null);focusCanvas();}}/>;})()}
          {quick && !quick.node && (quick.lineSource || quick.parent) && byId.has((quick.lineSource || quick.parent)!) && <svg className="kc-edges kc-draft-edge" aria-hidden="true"><path d={(()=>{const source=drawnById.get((quick.lineSource || quick.parent)!)??byId.get((quick.lineSource || quick.parent)!)!;if(!quick.linePort)return path(source,{...source,id:'new-draft',node_id:'new-draft',parent_id:quick.parent,sort_order:(quick.after||quick.before)?((byId.get((quick.before||quick.after)!)?.sort_order??byId.get((quick.before||quick.after)!)?.y??0)+(quick.before?-0.5:0.5)):Math.max(0,...nodes.filter(n=>n.parent_id===quick.parent).map(n=>n.sort_order??n.y))+1024,title:quick.title||'新主题',x:quick.position.x,y:quick.position.y,canvas_data:{manual_layout:quick.manualLayout,title_format:quick.titleFormat},attachments:undefined});const size=contentSizes([source]).get(source.id)!;const d=portDirection(nodes,source.id,quick.linePort),v=vectors[d],p=edgePoint(size,d,quick.linePort==='child'?24:16),start={x:source.x+p.x,y:source.y+p.y},tip=edgePoint({width:272,height:170},d==='right'?'left':d==='left'?'right':d==='up'?'down':'up'),end={x:quick.position.x+tip.x,y:quick.position.y+tip.y};return `M ${start.x} ${start.y} C ${start.x+v.x*50} ${start.y+v.y*50},${end.x-v.x*50} ${end.y-v.y*50},${end.x} ${end.y}`;})()} /></svg>}
          {(!quick?.node||quick.standalone) && renderQuickEditor(false)}
        </div>
        {groupSelection&&groupNode&&activeGroup&&<div role="toolbar" aria-label="所选概要或外框操作" className="kc-group-tools" onPointerDown={e=>e.stopPropagation()}><strong>{groupSelection.kind==='boundary'?'外框':'概要'}：{groupSelection.kind==='summary'?groupNode.title:activeGroup.title}</strong><button disabled={historyBlocked||!writable(groupNode)} onClick={()=>editGroup()}>修改名称</button><button disabled={historyBlocked||!writable(groupNode)} onClick={()=>openVisual(groupSelection.kind,groupNode,groupSelection.key)}>调整范围</button><button disabled={historyBlocked||!writable(groupNode)} onClick={()=>void deleteGroup()}>{groupSelection.kind==='boundary'?'删除外框（保留主题）':'删除概要（保留原分支）'}</button></div>}
        {branchPreview && <div className="kc-connection-status" role="status">{branchPreview.message} · Esc 取消</div>}
        {connection.preview && <svg className={'kc-connection-preview ' + (connection.preview.valid ? '' : 'invalid')} aria-label={connection.preview.reconnect ? '关联线重连预览' : '拉线预览'}>
          <path d={connection.preview.path} />
          <circle cx={connection.preview.reconnect?.endpoint === 'source' ? connection.preview.start.x : connection.preview.end.x} cy={connection.preview.reconnect?.endpoint === 'source' ? connection.preview.start.y : connection.preview.end.y} r="6" />
        </svg>}
        {connection.preview && <div className="kc-connection-status" role="status">{connection.preview.reason ? connection.preview.reason + ' · Esc 取消' : connection.preview.reconnect ? (connection.preview.target?'松手重新连接 · Esc 保留原线':'松手创建浮动主题并重新连接 · Esc 取消') : connection.preview.target ? '松手建立关联 · Esc 取消' : connection.preview.relationType ? '点击空白处创建浮动主题并连线 · Esc 取消' : connection.preview.port==='sibling' ? '松手创建同级主题 · Esc 取消' : '松手创建子主题 · Esc 取消'}</div>}
        {!nodes.length && <div className="kc-empty">双击画布，创建第一个主题或项目</div>}
        <div className="kc-hint">滚轮/双指平移 · ⌘/Ctrl+滚轮缩放 · 方向键随排列导航 · 朝分支方向展开/子级 · 反向收起/父级 · ⌘/ 或 Ctrl+/ 折叠 · 拖空白框选 · ⇧ / ⌘ / Ctrl 点击多选 · 剪切/复制/粘贴 ⌘ / Ctrl+X、C、V · Delete 删除 · 双击创建 · 拉线创建分支 · Tab 子级 · Enter 同级 · 双击标题 / F2 / 空格改名 · 中键 / Alt 拖动画布</div>
      </div>
      <aside className="kc-sidebar" aria-label="知识目录"><div role="tablist" className="kc-tabs">{(Object.keys(scopes) as KnowledgeScope[]).map(s => <button key={s} disabled={browseBlocked} role="tab" aria-selected={tab === s} onClick={() => { browseEpoch.current++; suppressReveal.current = false; cancelView(); nodeMotion.cancel(); setTab(s); setQuery(''); setMenu(null); }}>{scopes[s]}</button>)}</div>
        <div className="kc-search"><input aria-label="搜索当前范围标题与正文" maxLength={200} value={query} placeholder={'搜索' + (active?.title ?? scopes[tab]) + '标题与正文…'} onChange={e => setQuery(e.target.value)} /><small>当前范围：{active?.title ?? scopes[tab]} · 标题与正文</small></div>
        {!orgId && tab !== 'personal' && <p className="kc-muted">请先在主页面选择企业</p>}
        {query.trim() && active && <KnowledgeSearch key={identity + active.id + query.trim()} containerId={active.id} query={query.trim()} onLocate={id => { if (busy.current || unconfirmed.current) { locate(id); return; } const key = identity; void refresh().then(next => { if (identityRef.current === key) locate(id, next.nodes); }).catch(e => { if (identityRef.current === key) setError(message(e)); }); }} />}
        {scopeContainers.map(c => <section key={c.id}><button className={'kc-container ' + (c.id === active?.id ? 'active' : '')} onClick={() => selectContainer(c.id)}>{c.title}<small>{c.role === 'viewer' ? '只读' : c.role === 'manager' ? '可管理' : '可编辑'}</small></button>{!query.trim() && nodes.filter(n => n.container_id === c.id && !summaryParent(nodes,n)).sort(siblingCompare).map(n => nav(n))}</section>)}
      </aside>
    </main>}
    {mediaView&&<CanvasMediaPreview key={identity+mediaView.node.id+mediaView.source} node={mediaView.node} source={mediaView.source} onClose={()=>{setMediaView(null);focusCanvas();}}/>}
    {mediaEditor&&<CanvasMediaEditor restored={mediaEditor.draft} onDraft={mediaDraft} key={identity+mediaEditor.node.id+mediaEditor.mode} node={mediaEditor.node} mode={mediaEditor.mode} pending={pending} unknown={!!unconfirmed.current} saveError={error} onCheck={()=>void checkSave()} onUploaded={()=>refresh()} onSave={data=>saveAnnotations([{node:mediaEditor.node,data}])} onClose={()=>{setMediaEditor(null);focusCanvas();}}/>}
    {mapEditor&&loadedIdentity===identity&&<MapThemeEditor draft={mapEditor} nodes={nodes} source={toolNodes.length===1?toolNodes[0]:undefined} pending={pending} unknown={!!unconfirmed.current} error={error} onChange={setMapEditor} onSave={saveMapTheme} onCheck={()=>void checkSave()} onClose={()=>{if(unconfirmed.current){setError('保存结果尚未确认，请先核对。');return;}if(JSON.stringify(mapEditor.theme)!==JSON.stringify(normalizeMapTheme(active?.canvas_theme))||mapEditor.reset)closeDraft(()=>setMapEditor(null));else setMapEditor(null);focusCanvas();}}/>}
    {visual && <CanvasVisualEditor restored={visual.draft} onDraft={visualDraft} key={identity+visual.node.id+visual.mode} node={visual.mode==='boundary'&&visual.boundaryKey?{...visual.node,canvas_data:{...visual.node.canvas_data,boundary:readGroup(visual.node,{node:visual.node.id,kind:'boundary',key:visual.boundaryKey})}}:visual.node} mixedAlign={visual.mode==='style'&&new Set(toolNodes.map(n=>n.canvas_data?.title_format?.align??'left')).size>1} inheritedStyle={mapAppearance({...visual.node,canvas_data:{...visual.node.canvas_data,style:null}},nodes,active?.canvas_theme)?.style??undefined} nodes={nodes} mode={visual.mode} saveError={error} pending={pending} unknown={!!unconfirmed.current} onCheck={()=>void checkSave()} onUploaded={()=>refresh()} onSave={(data,scope)=>saveAnnotations(visual.mode==='style'?(visual.targets??toolNodes).map(node=>({node,data:{...editTopicStyle(node.title,node.canvas_data,data.style,mapAppearance(visual.node,nodes,active?.canvas_theme)?.style??defaultStyle,scope?.align,scope?.textMarks),...(data.width!==visual.node.canvas_data?.width?{width:data.width}:{})}})):[{node:visual.node,data:visual.mode==='boundary'&&visual.boundaryKey?setBoundary(visual.node.canvas_data,visual.boundaryKey,data.boundary??null):data}])} onClose={()=>{setVisual(null);focusCanvas();}}/>}
    {annotation && <CanvasAnnotationDialog restored={annotation.draft} onDraft={annotationDraft} key={identity+annotation.node.id+annotation.mode} node={annotation.node} mode={annotation.mode} pending={pending} unknown={!!unconfirmed.current} saveError={error} onCheck={()=>void checkSave()} writable={writable(annotation.node)} onSave={data=>saveAnnotations([{node:annotation.node,data}])} onClose={()=>{setAnnotation(null);focusCanvas();}}/>}
    {fileObject && loadedIdentity === identity && <KnowledgeFiles key={identity+fileObject.id} object={fileObject} nodes={nodes} onLabels={()=>{const n=byId.get(fileObject.id);if(n){setFileObject(null);setFileEntry(null);openAnnotation('labels',n);}}} entry={fileEntry?.action} position={fileEntry?.position} onClose={() => { setFileObject(null);setFileEntry(null); focusCanvas(); }} onBusyChange={setFilePending} onFilesChanged={files=>{const next={...sceneRef.current,nodes:sceneRef.current.nodes.map(n=>n.id===fileObject.id?{...n,attachments:{total:files.length,items:files.slice(0,2)}}:n)};sceneRef.current=next;setScene(next);void refresh().catch(()=>setError('附件已更新，画布同步失败，请刷新'));}} />}
    {menu && <CanvasContextMenu key={`${menu.x}:${menu.y}:${menu.parent}`} position={menu} onClose={() => setMenu(null)} onReturnFocus={focusCanvas}>
      {menu.group?<><CanvasMenuItem disabled={historyBlocked||!byId.get(menu.group.node)||!writable(byId.get(menu.group.node)!)} onSelect={()=>editGroup(menu.group)}>修改{menu.group.kind==='boundary'?'外框':'概要'}名称</CanvasMenuItem><CanvasMenuItem disabled={historyBlocked||!byId.get(menu.group.node)||!writable(byId.get(menu.group.node)!)} onSelect={()=>openVisual(menu.group!.kind,byId.get(menu.group!.node),menu.group!.key)}>调整范围</CanvasMenuItem><CanvasMenuItem disabled={historyBlocked||!byId.get(menu.group.node)||!writable(byId.get(menu.group.node)!)} onSelect={()=>void deleteGroup(menu.group)}>删除{menu.group.kind==='boundary'?'外框（保留主题）':'概要（保留原分支）'}</CanvasMenuItem></>:<>
      {(!menu.parent || (byId.get(menu.parent) && writable(byId.get(menu.parent)!))) && <>{menu.parent?<CanvasSubMenu label="修改排列类型" disabled={historyBlocked}>{topicStructures.map(type=><CanvasMenuItem key={type.id} onSelect={()=>void changeStructure(byId.get(menu.parent!)!,type.id)}>{type.title}{effectiveStructure(nodes,menu.parent!)===type.id?' ✓':''}</CanvasMenuItem>)}</CanvasSubMenu>:<CanvasSubMenu label="创建主题" disabled={historyBlocked||!active||active.role==='viewer'}>{topicStructures.map(type=><CanvasMenuItem key={type.id} onSelect={()=>createDefaultTopic(menu,type.id)}>{type.title}{type.id==='right'?' · 默认':''}</CanvasMenuItem>)}</CanvasSubMenu>}<CanvasSubMenu label="创建" disabled={historyBlocked}>{(['document', 'rule'] as const).map(type => <CanvasMenuItem key={type} onSelect={() => openCreate(menu.point, menu.parent, type)}>创建{kinds[type]}</CanvasMenuItem>)}{orgId && !menu.parent && <CanvasMenuItem onSelect={() => openCreate(menu.point, null, 'project')}>创建项目</CanvasMenuItem>}{menu.parent && writable(byId.get(menu.parent)!) && <CanvasMenuItem disabled={historyBlocked||toolNodes.length!==1} onSelect={()=>startParent(byId.get(menu.parent!)!)}>插入父主题 · {shortcut('⌘Enter','Ctrl+Enter')}</CanvasMenuItem>}</CanvasSubMenu></>}
      {menu.parent && byId.get(menu.parent) && <><CanvasMenuItem disabled={historyBlocked} onSelect={()=>openAnnotation('note',byId.get(menu.parent!)!)}>备注</CanvasMenuItem></>}
      <CanvasMenuItem disabled={historyBlocked || !chosen.size} onSelect={() => copyToSystem('cut')}>剪切整条分支 · {shortcut('⌘X','Ctrl+X')}</CanvasMenuItem><CanvasMenuItem disabled={historyBlocked || !chosen.size} onSelect={() => copyToSystem()}>复制整条分支 · {shortcut('⌘C','Ctrl+C')}</CanvasMenuItem><CanvasMenuItem disabled={historyBlocked || !clipboard} onSelect={() => void pasteSelection(clipboard, menu)}>粘贴分支 · {shortcut('⌘V','Ctrl+V')}</CanvasMenuItem><CanvasSubMenu label="删除" disabled={historyBlocked||!chosen.size}><CanvasMenuItem disabled={historyBlocked||toolNodes.length!==1||!writable(toolNodes[0])} onSelect={()=>void deleteSingle(toolNodes[0])}>只删除当前主题（保留子主题） · {shortcut('⌘⌫','Ctrl+Delete')}</CanvasMenuItem><CanvasMenuItem disabled={historyBlocked || !chosen.size} onSelect={() => void deleteSelection()}>删除整条分支 · {shortcut('⌫','Delete')}</CanvasMenuItem></CanvasSubMenu>
      <CanvasSubMenu label="分支" disabled={browseBlocked||!nodes.some(n=>n.parent_id)}><CanvasMenuItem disabled={browseBlocked||!nodes.some(n=>n.parent_id)} onSelect={()=>foldAll(false,menu.parent?[menu.parent]:undefined)}>折叠全部后代 · {shortcut('⌥⇧↑','Alt+Shift+↑')}</CanvasMenuItem><CanvasMenuItem disabled={browseBlocked||!nodes.some(n=>n.parent_id)} onSelect={()=>foldAll(true,menu.parent?[menu.parent]:undefined)}>展开全部后代 · {shortcut('⌥⇧↓','Alt+Shift+↓')}</CanvasMenuItem></CanvasSubMenu>
      {menu.parent && byId.get(menu.parent) && <CanvasSubMenu label="更多"><CanvasMenuItem onSelect={()=>{setBodyHistory(menu.parent!);setMenu(null);}}>历史版本</CanvasMenuItem>{container(byId.get(menu.parent)!.container_id)?.kind === 'project' && container(byId.get(menu.parent)!.container_id)?.role === 'manager' && <CanvasMenuItem onSelect={() => { const c = container(byId.get(menu.parent!)!.container_id)!; setMenu(null); const key = identity; knowledgeApi.members(c.id).then(rows => { if (identityRef.current === key) setMemberPanel({ id: c.id, title: c.title, rows }); }).catch(e => setError(message(e))); }}>项目成员权限</CanvasMenuItem>}</CanvasSubMenu>}</>}
    </CanvasContextMenu>}
    {status === 'ready' && draftStore.recovery && <Dialog motionPreset="canvas" open onOpenChange={() => {}} title="恢复未保存草稿" showClose={false} closeOnOutsideClick={false} closeOnEscape={false} onCloseAutoFocus={returnFromDialog} className="kc-modal"><div className="kc-form"><p>发现此账号、当前企业尚未保存的画布草稿（本浏览器保留 7 天）。请先恢复或丢弃，再继续编辑。</p><label>选择草稿<select aria-label="选择恢复草稿" value={draftStore.selected} onChange={e => draftStore.select(e.target.value)}>{draftStore.recoveries.map(r => <option key={r.id} value={r.id}>{new Date(r.saved).toLocaleString()} · 草稿 {r.id.slice(0, 6)}</option>)}</select></label>{draftStore.recovery.attempt && <p>有一次保存结果尚未确认。恢复后请点击刷新核对；丢弃草稿不会撤回已经发送的操作。</p>}<button disabled={pending || !!quick || !!draft || !!editing} onClick={() => void restoreDraft()}>恢复草稿</button><button onClick={() => { if (window.confirm('永久丢弃这份未保存草稿？') && !draftStore.clear()) setError('草稿正在其他标签页编辑或未能删除，请稍后重试。'); }}>丢弃草稿</button>{error && <p role="alert">{error}</p>}</div></Dialog>}
    {dirty && draftStore.storageError && <p role="alert">浏览器无法保存恢复草稿，请先保存内容再离开；可先复制到其他位置。</p>}
    {draft && <Dialog motionPreset="canvas" open title="创建内容" hideTitleVisually showClose={false} closeOnOutsideClick={false} closeOnEscape={!pending} onOpenChange={open => { if (!open && !pending) closeDraft(() => setDraft(null)); }} onCloseAutoFocus={returnFromDialog} className="kc-modal"><div className="kc-form"><form onSubmit={e => { e.preventDefault(); void saveDraft(); }}>
      <h2>创建内容</h2>{draft.created && <small role="status">节点已创建；新输入仍保留，继续保存将更新这个节点。</small>}<label>类型<select value={draft.type} disabled={pending || !!draft.created || !!unconfirmed.current} onChange={e => setDraft({ ...draft, type: e.target.value as Draft['type'] })}>{(Object.keys(kinds) as Draft['type'][]).filter(t => t !== 'project' || (orgId && !draft.parent)).map(t => <option key={t} value={t}>{kinds[t]}</option>)}</select></label>
      {draft.type !== 'project' && <label>归属<select value={draft.container} disabled={pending || !!draft.parent || !!draft.created || !!unconfirmed.current} required onChange={e => setDraft({ ...draft, container: e.target.value })}><option value="" disabled>选择知识库或项目</option>{scene.containers.filter(c => c.role !== 'viewer').map(c => <option key={c.id} value={c.id}>{scopes[c.kind]} · {c.title}</option>)}</select></label>}
      <label>标题<input autoFocus required maxLength={200} value={draft.title} onChange={e => setDraft({ ...draft, title: e.target.value })} /></label>{draft.type !== 'project' && <label>正文<textarea value={draft.body} maxLength={200000} onChange={e => setDraft({ ...draft, body: e.target.value })} /></label>}
      {error && <p role="alert">{error}</p>}<footer>{unconfirmed.current && <button type="button" disabled={pending} onClick={() => void checkSave()}>核对保存结果</button>}<button type="button" disabled={pending} onClick={() => closeDraft(() => setDraft(null))}>取消</button><button className="primary" disabled={pending}>保存到画布</button></footer>
    </form></div></Dialog>}
    {filePreview?.identity===identity&&<BoundFilePreview key={identity+filePreview.id+filePreview.version.binding_id+filePreview.version.revision} objectId={filePreview.id} version={filePreview.version} onClose={()=>{setFilePreview(null);focusCanvas();}}/>}
    {imageView?.identity===identity&&<ImagePreviewModal imageUrl={imageView.src} filename={imageView.title} onClose={()=>{setImageView(null);focusCanvas();}}/>}
    {bodyHistory&&<Dialog motionPreset="canvas" open title="历史版本" onOpenChange={open=>{if(!open)setBodyHistory(null);}}><KnowledgeHistory objectId={bodyHistory}/></Dialog>}
    {memberPanel && <Dialog motionPreset="canvas" open title="项目成员权限" hideTitleVisually showClose={false} closeOnOutsideClick={false} closeOnEscape={!pending} onOpenChange={open => { if (!open && !pending) setMemberPanel(null); }} onCloseAutoFocus={returnFromDialog} className="kc-modal"><div className="kc-form"><h2>{memberPanel.title} · 成员权限</h2>{memberPanel.rows.map(m => <div className="kc-member" key={m.user_id}><span>{m.nickname || m.user_id.slice(0, 8)}{m.user_id === userId ? '（你）' : ''}</span><select aria-label={'权限 ' + m.nickname} disabled={pending || m.user_id === userId} value={m.status === 'active' ? m.role ?? '' : ''} onChange={e => { const role = e.target.value as KnowledgeRole | ''; const key = identity; void act('set_member', { container_id: memberPanel.id, user_id: m.user_id, role: role || 'viewer', status: role ? 'active' : 'removed' }).then(async ok => { if (ok) { const rows = await knowledgeApi.members(memberPanel.id); if (identityRef.current === key) setMemberPanel(p => p ? { ...p, rows } : p); } }).catch(e => setError(message(e))); }}><option value="">未加入</option><option value="viewer">查看</option><option value="editor">编辑</option><option value="manager">管理</option></select></div>)}{error && <p role="alert">{error}</p>}<footer><button disabled={pending} onClick={() => setMemberPanel(null)}>关闭</button></footer></div></Dialog>}
  </div></CanvasSurfaceMotion>;
}
