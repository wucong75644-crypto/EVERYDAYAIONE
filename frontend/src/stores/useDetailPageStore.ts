import { create } from 'zustand';
import { archiveDetailProject, attachDetailImage, createDetailProject, listDetailProjects, refreshDetailTaskStatuses, removeDetailImage, saveDetailSettings,
  getDetailProject, startDetailProject, getDetailCapabilities } from '../services/detailProject';
import { uploadImageFile } from '../services/upload';
import { toApiRequestError } from '../services/api';
import type { DetailGenerationForm, DetailLocalImage, DetailProjectDraft, DetailGroup, DetailGenerationRun, PromptModelOption, DetailTaskSummary } from '../types/detailPage';

export const DEFAULT_FORM: DetailGenerationForm = {contentType:'default',platform:'taobao',requirement:'',language:'zh-CN',aspectRatio:'1:1',quality:'1k',count:14,promptModel:'gemini-3.8-flash'};
interface DetailPageState {
  images: DetailLocalImage[]; form: DetailGenerationForm; groups: DetailGroup[];
  runs: DetailGenerationRun[]; currentRunId: string|null;
  status: string; models: PromptModelOption[]; ratios: string[]; enabled: boolean;
  isTransitioning: boolean; isUploading: boolean; formError: string | null; projectId: string | null;
  projectVersion: number | null; isHydrating: boolean;
  tasks: DetailTaskSummary[]; taskCursor: string|null; isLoadingTasks: boolean; taskError: string|null; scopeKey: string|null; isMutating: boolean;
  hydrateDraft: (scopeKey?: string, preferredId?: string|null) => Promise<void>;
  selectTask: (id:string) => Promise<void>; createTask: () => Promise<void>;
  deleteTask: (id:string) => Promise<void>;
  loadMoreTasks: () => Promise<void>; refreshTasks: () => Promise<void>;
  refresh: () => Promise<void>;
  attachWorkspaceImages: (category: DetailLocalImage['category'],paths:string[]) => Promise<void>;
  addImages: (category: DetailLocalImage['category'],files:File[]) => Promise<void>;
  removeImage: (id:string) => Promise<void>;
  updateForm: (patch:Partial<DetailGenerationForm>) => void;
  startAnalysis: () => Promise<void>; restart: () => Promise<void>; reset: () => void;
}
const initialState = {tasks:[] as DetailTaskSummary[],taskCursor:null as string|null,isLoadingTasks:false,
  taskError:null as string|null,scopeKey:null as string|null,isMutating:false,images: [] as DetailLocalImage[], form: {...DEFAULT_FORM},groups: [] as DetailGroup[],
  runs:[] as DetailGenerationRun[],currentRunId:null as string|null,
  status:'draft',models: [] as PromptModelOption[],ratios:['1:1','3:4','4:5','16:9'],enabled:false,
  isTransitioning:false,isUploading:false,formError:null as string|null,projectId:null as string|null,projectVersion:null as number|null,isHydrating:false};
const ALLOWED_IMAGE_TYPES = new Set(['image/jpeg','image/png','image/webp']);
const MAX_IMAGES=9;
const EDITABLE_STATUSES=['draft','completed','failed'];
let settingsTimer: ReturnType<typeof setTimeout> | null=null;
let pollTimer: ReturnType<typeof setTimeout> | null=null;
let saving: Promise<void>=Promise.resolve();
let lifecycleVersion=0;
let createRequestId: string|null=null;
let listTimer: ReturnType<typeof setTimeout>|null=null;
const runRequests=new Map<string,string>();
let formRevision=0, savedRevision=0;
function remembered(scope:string){try{return localStorage.getItem(`detail-task:${scope}`);}catch{return null;}}
function remember(id:string){const scope=useDetailPageStore.getState().scopeKey;if(scope)try{localStorage.setItem(`detail-task:${scope}`,id);}catch{/* Storage may be disabled. */}}
function valid(epoch:number,id?:string|null){return epoch===lifecycleVersion&&(!id||useDetailPageStore.getState().projectId===id);}
function adopt(project:DetailProjectDraft){
  useDetailPageStore.getState().images.forEach(releasePreview);formRevision=0;savedRevision=0;
  useDetailPageStore.setState({...applyDraft(project),isTransitioning:false,isHydrating:false});remember(project.id);schedulePoll();scheduleListPoll();
}
function scheduleListPoll(){
  if(listTimer)clearTimeout(listTimer);
  listTimer=setTimeout(()=>{void useDetailPageStore.getState().refreshTasks();},typeof document!=='undefined'&&document.hidden?30000:5000);
}
function clearSettingsTimer(){if(settingsTimer)clearTimeout(settingsTimer);settingsTimer=null;}
function releasePreview(image:DetailLocalImage){if(image.previewUrl.startsWith('blob:'))URL.revokeObjectURL(image.previewUrl);}
function applyDraft(project:DetailProjectDraft|null){
  if(!project)return {...initialState,form:{...DEFAULT_FORM}};
  return {projectId:project.id,projectVersion:project.version,status:project.status??'draft',groups:project.groups??[],
    runs:project.runs??[],currentRunId:project.run_state?.run_id??null,
    form:{contentType:project.content_type,platform:project.platform,requirement:project.requirement,
      language:project.language,aspectRatio:project.aspect_ratio,quality:project.quality,
      count:project.content_type==='default'?14:project.image_count,promptModel:project.prompt_model??'gemini-3.8-flash'},
    images:project.images.map((image):DetailLocalImage=>({id:image.id,category:image.category,workspacePath:image.workspace_path,
      previewUrl:image.thumbnail_url||image.original_url||'',originalUrl:image.original_url||undefined,error:null,
      status:image.status,sortOrder:image.sort_order,name:image.workspace_path.split('/').pop()||'图片'}))};
}
function persistSettings(allowBusy=false){
  const epoch=lifecycleVersion, id=useDetailPageStore.getState().projectId;
  saving=saving.catch(()=>{}).then(async()=>{
    while(valid(epoch,id)){
      const {projectId,projectVersion,form,status,isUploading,isMutating}=useDetailPageStore.getState();
      if(!projectId||projectVersion===null||!EDITABLE_STATUSES.includes(status)||((isUploading||isMutating)&&!allowBusy)||savedRevision===formRevision)return;
      const revision=formRevision;
      const saved=await saveDetailSettings(projectId,projectVersion,form);
      if(!valid(epoch,id))return;
      if(!saved||saved.id!==projectId)throw new Error('草稿保存失败，请重试');
      savedRevision=revision;useDetailPageStore.setState({projectVersion:saved.version});
    }
  });
  return saving;
}
function schedulePoll(){
  if(pollTimer)clearTimeout(pollTimer);
  if(['analyzing','plan_ready','generating'].includes(useDetailPageStore.getState().status))
    pollTimer=setTimeout(()=>{void useDetailPageStore.getState().refresh();},1500);
}
export const useDetailPageStore=create<DetailPageState>((set,get)=>({
  ...initialState,
  hydrateDraft:async(scopeKey='personal',preferredId)=>{
    const pendingCreate=get().scopeKey===scopeKey?createRequestId:null;
    get().reset();createRequestId=pendingCreate;set({scopeKey,isHydrating:true});
    const epoch=lifecycleVersion;
    try{
      const [list,capabilities]=await Promise.all([listDetailProjects(),getDetailCapabilities()]);
      if(!valid(epoch))return;
      set({tasks:list.items,taskCursor:list.next_cursor,models:capabilities.prompt_models,enabled:capabilities.enabled,
        ratios:capabilities.image_models[0]?.aspect_ratios??initialState.ratios});
      const preferred=preferredId||remembered(scopeKey);
      let project:DetailProjectDraft|null=null;
      if(preferred){try{project=await getDetailProject(preferred);if(project?.status==='archived')project=null;}
        catch{if(valid(epoch))set({taskError:'上次任务不可访问，已返回任务列表'});}}
      if(!valid(epoch))return;
      if(!project&&list.items[0])project=await getDetailProject(list.items[0].id);
      if(!valid(epoch))return;
      if(!project){createRequestId??=crypto.randomUUID();project=await createDetailProject(createRequestId);if(valid(epoch))createRequestId=null;}
      if(!valid(epoch))return;
      if(!project)throw new Error('任务读取失败，请重试');
      adopt(project);void get().refreshTasks();
    }catch(error){if(valid(epoch))set({isHydrating:false,formError:toApiRequestError(error).message});}
  },
  selectTask:async(id)=>{
    if(id===get().projectId||get().isTransitioning||get().isUploading||get().isMutating||get().isHydrating)return;
    let epoch=lifecycleVersion;set({isTransitioning:true,formError:null});clearSettingsTimer();
    try{
      await persistSettings();if(!valid(epoch))return;
      epoch=++lifecycleVersion;set({isLoadingTasks:false});if(pollTimer)clearTimeout(pollTimer);
      const project=await getDetailProject(id);
      if(!valid(epoch))return;
      if(!project||project.status==='archived')throw new Error('任务不存在或已归档');
      adopt(project);
    }catch(error){if(valid(epoch)){set({isTransitioning:false,formError:toApiRequestError(error).message});schedulePoll();scheduleListPoll();}}
  },
  createTask:async()=>{
    if(get().isTransitioning||get().isUploading||get().isMutating||get().isHydrating)return;
    let epoch=lifecycleVersion;set({isTransitioning:true,formError:null});clearSettingsTimer();
    try{
      await persistSettings();if(!valid(epoch))return;
      epoch=++lifecycleVersion;set({isLoadingTasks:false});if(pollTimer)clearTimeout(pollTimer);
      createRequestId??=crypto.randomUUID();
      const project=await createDetailProject(createRequestId);
      if(!valid(epoch))return;
      if(!project)throw new Error('新建任务失败，请重试');
      createRequestId=null;adopt(project);await get().refreshTasks();
    }catch(error){if(valid(epoch)){set({isTransitioning:false,formError:toApiRequestError(error).message});schedulePoll();scheduleListPoll();}}
  },
  deleteTask:async(id)=>{
    if(get().isTransitioning||get().isUploading||get().isMutating||get().isHydrating)return;
    let epoch=lifecycleVersion;set({isTransitioning:true,taskError:null});clearSettingsTimer();
    try{
      if(id!==get().projectId)await persistSettings();
      if(!valid(epoch))return;
      await archiveDetailProject(id);
      if(!valid(epoch))return;
      epoch=++lifecycleVersion;
      const wasSelected=id===get().projectId;
      set(state=>({tasks:state.tasks.filter(task=>task.id!==id),isLoadingTasks:false}));
      runRequests.delete(id);
      if(wasSelected){
        get().images.forEach(releasePreview);formRevision=0;savedRevision=0;
        set({...applyDraft(null),tasks:get().tasks,taskCursor:get().taskCursor,scopeKey:get().scopeKey,models:get().models,
          ratios:get().ratios,enabled:get().enabled,isTransitioning:true});
        const next=get().tasks[0];
        let project:DetailProjectDraft|null;
        if(next)project=await getDetailProject(next.id);
        else{createRequestId??=crypto.randomUUID();project=await createDetailProject(createRequestId);}
        if(!valid(epoch))return;
        if(!project||project.status==='archived')throw new Error('任务已删除，请重新加载任务列表');
        createRequestId=null;adopt(project);
      }else set({isTransitioning:false});
      void get().refreshTasks();schedulePoll();
    }catch(error){if(valid(epoch))set({isTransitioning:false,taskError:toApiRequestError(error).message});}
    finally{if(valid(epoch)){schedulePoll();scheduleListPoll();}}
  },
  loadMoreTasks:async()=>{
    const {taskCursor,isLoadingTasks}=get();if(!taskCursor||isLoadingTasks)return;
    const epoch=lifecycleVersion;set({isLoadingTasks:true});
    try{const list=await listDetailProjects(taskCursor);if(!valid(epoch))return;
      set(state=>({tasks:[...state.tasks,...list.items.filter(item=>!state.tasks.some(t=>t.id===item.id))],taskCursor:list.next_cursor,taskError:null}));
    }catch(error){if(valid(epoch))set({taskError:toApiRequestError(error).message});}
    finally{if(valid(epoch))set({isLoadingTasks:false});}
  },
  refreshTasks:async()=>{
    const epoch=lifecycleVersion;
    try{
      const first=await listDetailProjects();if(!valid(epoch))return;
      const loaded=get().tasks.filter(t=>!first.items.some(item=>item.id===t.id));
      const active=loaded.filter(t=>!['draft','completed','failed','archived'].includes(t.status));
      const updates:DetailTaskSummary[]=[];
      for(let i=0;i<active.length;i+=100){updates.push(...await refreshDetailTaskStatuses(active.slice(i,i+100).map(t=>t.id)));if(!valid(epoch))return;}
      set({tasks:[...first.items,...get().tasks.filter(t=>!first.items.some(item=>item.id===t.id)).map(t=>updates.find(u=>u.id===t.id)??t)]
        .sort((a,b)=>b.created_at.localeCompare(a.created_at)||b.id.localeCompare(a.id)),
        taskCursor:loaded.length?get().taskCursor:first.next_cursor,taskError:null});
    }catch(error){if(valid(epoch))set({taskError:toApiRequestError(error).message});}
    finally{if(valid(epoch))scheduleListPoll();}
  },
  refresh:async()=>{
    const {projectId}=get();const epoch=lifecycleVersion;if(!projectId)return;
    try{const project=await getDetailProject(projectId);
      if(epoch!==lifecycleVersion)return;
      if(project&&!get().isTransitioning&&!get().isMutating)set({status:project.status??'draft',groups:project.groups??[],
        runs:project.runs??[],currentRunId:project.run_state?.run_id??null,
        ...(savedRevision===formRevision?{projectVersion:Math.max(get().projectVersion??0,project.version)}:{}),formError:null});
    }catch(error){if(epoch!==lifecycleVersion)return;set({formError:toApiRequestError(error).message});}
    schedulePoll();
  },
  attachWorkspaceImages: async (category, paths) => {
    if(get().isUploading||get().isTransitioning||get().isMutating||get().status!=='draft'||!get().projectId)return;
    const epoch=lifecycleVersion, projectId=get().projectId!;
    if (get().images.length + paths.length > MAX_IMAGES) {
      set({ formError: `产品图和参考图合计最多上传 ${MAX_IMAGES} 张` });
      return;
    }
    clearSettingsTimer();set({isUploading:true});
    try { await persistSettings(true); } catch(error){
      if(valid(epoch))set({isUploading:false,formError:toApiRequestError(error).message});
      return;
    }
    if(!valid(epoch,projectId))return;
    set({isUploading:true,isMutating:false});
    try { for (const path of paths) {
      try {
        const project = await attachDetailImage(path, category, projectId);
        if(epoch!==lifecycleVersion)return;
        if(!project||project.id!==projectId)throw new Error('图片关联结果无效，请重新加载任务');
        set((state) => ({ ...applyDraft(project), form: state.form, formError: null }));
      } catch (error) {
        if(epoch!==lifecycleVersion)return;
        set({ formError: toApiRequestError(error).message });
        break;
      }
    }
    } finally { if(epoch===lifecycleVersion){set({isUploading:false});get().updateForm({});} }
  },
  addImages: async (category, files) => {
    if(get().isUploading||get().isTransitioning||get().isMutating||get().status!=='draft'||!get().projectId)return;
    const epoch=lifecycleVersion, projectId=get().projectId!;
    const currentImages = get().images;
    if (currentImages.length + files.length > MAX_IMAGES) {
      set({ formError: `产品图和参考图合计最多上传 ${MAX_IMAGES} 张` });
      return;
    }
    const invalidFile = files.find((file) => !ALLOWED_IMAGE_TYPES.has(file.type));
    if (invalidFile) {
      set({ formError: '仅支持 JPG、PNG、WebP 格式的图片' });
      return;
    }
    const newImages = files.map((file, index): DetailLocalImage => ({
      id: `${Date.now()}-${index}-${file.name}`,
      category,
      file,
      previewUrl: URL.createObjectURL(file),
      error: null,
      status: 'local',
      name: file.name,
    }));
    set((state) => ({ images: [...state.images, ...newImages], formError: null }));
    clearSettingsTimer();set({isUploading:true});
    try { await persistSettings(true); } catch(error){
      if(valid(epoch))set(state=>({isUploading:false,formError:toApiRequestError(error).message,
        images:state.images.map(image=>newImages.some(item=>item.id===image.id)?{...image,status:'failed',error:toApiRequestError(error).message}:image)}));
      return;
    }
    if(!valid(epoch,projectId))return;
    set({isUploading:true,isMutating:false});
    try { for (const image of newImages) {
      try {
        set((state) => ({ images: state.images.map((item) => item.id === image.id ? { ...item, status: 'uploading' } : item) }));
        const uploaded = await uploadImageFile(image.file!);
        if(epoch!==lifecycleVersion)return;
        if (!uploaded.workspace_path) throw new Error('上传结果缺少工作区路径');
        set((state) => ({ images: state.images.map((item) => item.id === image.id ? { ...item, status: 'attaching', workspacePath: uploaded.workspace_path } : item) }));
        const project = await attachDetailImage(uploaded.workspace_path, category, projectId);
        if(epoch!==lifecycleVersion)return;
        if(!project||project.id!==projectId)throw new Error('图片关联结果无效，请重新加载任务');
        const remotePreview = uploaded.thumbnail_url || uploaded.preview_url || uploaded.url;
        const requestVersion = lifecycleVersion;
        set((state) => {
          const draft = applyDraft(project);
          draft.form = state.form;
          const images = draft.images.map((item) => item.workspacePath === uploaded.workspace_path
            ? { ...item, previewUrl: image.previewUrl }
            : item);
          const pending = state.images.filter((item) => item.id !== image.id && ['local', 'uploading', 'attaching', 'failed'].includes(item.status));
          return { ...draft, images: [...images, ...pending], formError: null };
        });
        if (remotePreview) {
          const remoteImage = new Image();
          remoteImage.onload = () => {
            if (requestVersion !== lifecycleVersion) return;
            set((state) => ({ images: state.images.map((item) => item.workspacePath === uploaded.workspace_path
              ? { ...item, previewUrl: remotePreview }
              : item) }));
            setTimeout(() => URL.revokeObjectURL(image.previewUrl), 30000);
          };
          remoteImage.src = remotePreview;
        }
      } catch (error) {
        if(epoch!==lifecycleVersion)return;
        set((state) => ({ images: state.images.map((item) => item.id === image.id ? { ...item, status: 'failed', error: toApiRequestError(error).message } : item), formError: toApiRequestError(error).message }));
      }
    }
    } finally { if(epoch===lifecycleVersion){set({isUploading:false});get().updateForm({});} }
  },
  removeImage: async (id) => {
    if(get().isTransitioning||get().isMutating||get().isUploading||get().status!=='draft')return;
    const image=get().images.find(item=>item.id===id);if(!image)return;
    const epoch=lifecycleVersion, projectId=get().projectId;
    if(!projectId||image.status==='failed'||!image.workspacePath){releasePreview(image);set(state=>({images:state.images.filter(item=>item.id!==id)}));return;}
    set({isMutating:true});clearSettingsTimer();
    try{
      await persistSettings(true);if(!valid(epoch,projectId))return;
      const project=await removeDetailImage(projectId,id,get().projectVersion!);
      if(valid(epoch,projectId)&&project)set(state=>({...applyDraft(project),form:state.form,formError:null}));
    }catch(error){if(valid(epoch,projectId))set({formError:toApiRequestError(error).message});}
    finally{if(valid(epoch,projectId)){set({isMutating:false});get().updateForm({});}}
  },
  updateForm:(patch)=>{
    if(!EDITABLE_STATUSES.includes(get().status)||get().isTransitioning||get().isHydrating)return;
    if(get().status!=='draft'&&Object.keys(patch).some(key=>key!=='requirement'))return;
    formRevision++;
    set(state=>{const next={...state.form,...patch};
      if(patch.contentType){next.count=patch.contentType==='default'?14:patch.count??(state.form.contentType==='default'?7:state.form.count);
        if(!patch.aspectRatio)next.aspectRatio=patch.contentType==='detail_page'?'3:4':'1:1';}
      return {form:next};});
    clearSettingsTimer();const epoch=lifecycleVersion;settingsTimer=setTimeout(()=>{void persistSettings().catch(error=>{if(valid(epoch))set({formError:toApiRequestError(error).message});});},500);
  },
  startAnalysis:async()=>{
    if(get().isTransitioning||get().isUploading||get().isMutating||!EDITABLE_STATUSES.includes(get().status))return;
    if(!get().images.some(image=>image.category==='product'&&image.status==='ready')||get().images.some(image=>image.status!=='ready')){
      set({formError:'请先完成产品图片上传'});return;
    }
    const selected=get().models.find(model=>model.id===get().form.promptModel);
    if(!get().enabled||!selected?.available){set({formError:selected?.reason||'主图详情生成尚未开放'});return;}
    const epoch=lifecycleVersion,sourceId=get().projectId;let requestId=sourceId?runRequests.get(sourceId):undefined;
    set({isTransitioning:true,formError:null});clearSettingsTimer();
    try{
      if(requestId&&sourceId){
        const existing=await getDetailProject(sourceId);if(!valid(epoch,sourceId))return;
        if(existing?.run_state?.request_id===requestId){runRequests.delete(sourceId);lifecycleVersion++;adopt(existing);return;}
      }
      await persistSettings();if(!valid(epoch,sourceId))return;
      const {projectId,projectVersion}=get();if(!projectId||projectVersion===null)throw new Error('项目尚未保存');
      requestId??=crypto.randomUUID();runRequests.set(projectId,requestId);
      const project=await startDetailProject(projectId,projectVersion,requestId);
      if(!valid(epoch,sourceId))return;
      if(!project||project.id!==projectId)throw new Error('任务受理结果尚未确认，请重试核实');
      runRequests.delete(projectId);lifecycleVersion++;adopt(project);
      void get().refreshTasks();
      schedulePoll();
    }catch(error){if(!valid(epoch,sourceId))return;const failure=toApiRequestError(error);
      if(failure.status&&failure.status>=400&&failure.status<500&&sourceId)runRequests.delete(sourceId);
      set({isTransitioning:false,formError:failure.message});
      // An unknown HTTP outcome retains the same request key for a safe retry.
    }
  },
  restart:async()=>get().createTask(),
  reset:()=>{
    lifecycleVersion++;clearSettingsTimer();if(pollTimer)clearTimeout(pollTimer);pollTimer=null;
    if(listTimer)clearTimeout(listTimer);listTimer=null;saving=Promise.resolve();formRevision=0;savedRevision=0;
    get().images.forEach(releasePreview);createRequestId=null;runRequests.clear();set({...initialState,form:{...DEFAULT_FORM}});
  },
}));
