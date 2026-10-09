import { create } from 'zustand';
import { attachDetailImage, getCurrentDetailProject, removeDetailImage, saveDetailSettings,
  getDetailProject, startDetailProject, getDetailCapabilities, archiveDetailProject } from '../services/detailProject';
import { uploadImageFile } from '../services/upload';
import { toApiRequestError } from '../services/api';
import type { DetailGenerationForm, DetailLocalImage, DetailProjectDraft, DetailGroup, PromptModelOption } from '../types/detailPage';

export const DEFAULT_FORM: DetailGenerationForm = {contentType:'default',platform:'taobao',requirement:'',language:'zh-CN',aspectRatio:'1:1',quality:'1k',count:14,promptModel:'kimi-k3'};
interface DetailPageState {
  images: DetailLocalImage[]; form: DetailGenerationForm; groups: DetailGroup[];
  status: string; models: PromptModelOption[]; ratios: string[]; enabled: boolean;
  isTransitioning: boolean; isUploading: boolean; formError: string | null; projectId: string | null;
  projectVersion: number | null; isHydrating: boolean;
  hydrateDraft: () => Promise<void>; refresh: () => Promise<void>;
  attachWorkspaceImages: (category: DetailLocalImage['category'],paths:string[]) => Promise<void>;
  addImages: (category: DetailLocalImage['category'],files:File[]) => Promise<void>;
  removeImage: (id:string) => Promise<void>;
  updateForm: (patch:Partial<DetailGenerationForm>) => void;
  startAnalysis: () => Promise<void>; restart: () => Promise<void>; reset: () => void;
}
const initialState = {images: [] as DetailLocalImage[], form: {...DEFAULT_FORM},groups: [] as DetailGroup[],
  status:'draft',models: [] as PromptModelOption[],ratios:['1:1','3:4','16:9'],enabled:false,
  isTransitioning:false,isUploading:false,formError:null as string|null,projectId:null as string|null,projectVersion:null as number|null,isHydrating:false};
const ALLOWED_IMAGE_TYPES = new Set(['image/jpeg','image/png','image/webp']);
const MAX_IMAGES=9;
let settingsTimer: ReturnType<typeof setTimeout> | null=null;
let pollTimer: ReturnType<typeof setTimeout> | null=null;
let saving: Promise<void>=Promise.resolve();
let lifecycleVersion=0;
let requestId: string|null=null;
function clearSettingsTimer(){if(settingsTimer)clearTimeout(settingsTimer);settingsTimer=null;}
function releasePreview(image:DetailLocalImage){if(image.previewUrl.startsWith('blob:'))URL.revokeObjectURL(image.previewUrl);}
function applyDraft(project:DetailProjectDraft|null){
  if(!project)return {...initialState,form:{...DEFAULT_FORM}};
  return {projectId:project.id,projectVersion:project.version,status:project.status??'draft',groups:project.groups??[],
    form:{contentType:project.content_type,platform:project.platform,requirement:project.requirement,
      language:project.language,aspectRatio:project.aspect_ratio,quality:project.quality,
      count:project.content_type==='default'?14:project.image_count,promptModel:project.prompt_model??'kimi-k3'},
    images:project.images.map((image):DetailLocalImage=>({id:image.id,category:image.category,workspacePath:image.workspace_path,
      previewUrl:image.thumbnail_url||image.original_url||'',originalUrl:image.original_url||undefined,error:null,
      status:image.status,sortOrder:image.sort_order,name:image.workspace_path.split('/').pop()||'图片'}))};
}
function persistSettings(){
  const epoch=lifecycleVersion;
  saving=saving.catch(()=>{}).then(async()=>{
    const {projectId,projectVersion,form,status,isUploading}=useDetailPageStore.getState();
    if(!projectId||projectVersion===null||status!=='draft'||isUploading||epoch!==lifecycleVersion)return;
    const saved=await saveDetailSettings(projectId,projectVersion,form);
    if(saved&&epoch===lifecycleVersion)useDetailPageStore.setState({projectVersion:saved.version});
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
  hydrateDraft:async()=>{
    const epoch=lifecycleVersion;set({isHydrating:true,formError:null});
    try{
      const [project,capabilities]=await Promise.all([getCurrentDetailProject(),getDetailCapabilities()]);
      if(epoch!==lifecycleVersion)return;
      set({...applyDraft(project),models:capabilities.prompt_models,enabled:capabilities.enabled,
        ratios:capabilities.image_models[0]?.aspect_ratios??initialState.ratios,isHydrating:false});
      schedulePoll();
    }catch(error){if(epoch===lifecycleVersion)set({isHydrating:false,formError:toApiRequestError(error).message});}
  },
  refresh:async()=>{
    const {projectId}=get();const epoch=lifecycleVersion;if(!projectId)return;
    try{const project=await getDetailProject(projectId);
      if(epoch!==lifecycleVersion)return;
      if(project)set({status:project.status??'draft',groups:project.groups??[],formError:null});
    }catch(error){if(epoch!==lifecycleVersion)return;set({formError:toApiRequestError(error).message});}
    schedulePoll();
  },
  attachWorkspaceImages: async (category, paths) => {
    if(get().isUploading||get().status!=='draft')return;
    const epoch=lifecycleVersion;
    if (get().images.length + paths.length > MAX_IMAGES) {
      set({ formError: `产品图和参考图合计最多上传 ${MAX_IMAGES} 张` });
      return;
    }
    set({isUploading:true});
    try { for (const path of paths) {
      try {
        const project = await attachDetailImage(path, category);
        if(epoch!==lifecycleVersion)return;
        set((state) => ({ ...applyDraft(project), form: state.form, formError: null }));
      } catch (error) {
        if(epoch!==lifecycleVersion)return;
        set({ formError: toApiRequestError(error).message });
        break;
      }
    }
    } finally { if(epoch===lifecycleVersion)set({isUploading:false}); }
  },
  addImages: async (category, files) => {
    if(get().isUploading||get().status!=='draft')return;
    const epoch=lifecycleVersion;
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
    set({isUploading:true});
    try { for (const image of newImages) {
      try {
        set((state) => ({ images: state.images.map((item) => item.id === image.id ? { ...item, status: 'uploading' } : item) }));
        const uploaded = await uploadImageFile(image.file!);
        if(epoch!==lifecycleVersion)return;
        if (!uploaded.workspace_path) throw new Error('上传结果缺少工作区路径');
        set((state) => ({ images: state.images.map((item) => item.id === image.id ? { ...item, status: 'attaching', workspacePath: uploaded.workspace_path } : item) }));
        const project = await attachDetailImage(uploaded.workspace_path, category);
        if(epoch!==lifecycleVersion)return;
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
    } finally { if(epoch===lifecycleVersion)set({isUploading:false}); }
  },
  removeImage: async (id) => {
    const image = get().images.find((item) => item.id === id);
    if (!image) return;
    if (!get().projectId || get().projectVersion === null || image.status !== 'ready') {
      releasePreview(image);
      set((state) => ({ images: state.images.filter((item) => item.id !== id), formError: null }));
      return;
    }
    try {
      const project = await removeDetailImage(get().projectId!, id, get().projectVersion!);
      set((state) => ({ ...applyDraft(project), form: state.form, formError: null }));
    } catch (error) {
      set({ formError: toApiRequestError(error).message });
    }
  },
  updateForm:(patch)=>{
    set(state=>{const next={...state.form,...patch};
      if(patch.contentType){next.count=patch.contentType==='default'?14:(state.form.contentType==='default'?7:state.form.count);
        if(!patch.aspectRatio)next.aspectRatio=patch.contentType==='detail_page'?'3:4':'1:1';}
      return {form:next};});
    clearSettingsTimer();settingsTimer=setTimeout(()=>{void persistSettings().catch(error=>set({formError:toApiRequestError(error).message}));},500);
  },
  startAnalysis:async()=>{
    if(get().isTransitioning||get().isUploading||get().status!=='draft')return;
    if(!get().images.some(image=>image.category==='product'&&image.status==='ready')||get().images.some(image=>image.status!=='ready')){
      set({formError:'请先完成产品图片上传'});return;
    }
    const selected=get().models.find(model=>model.id===get().form.promptModel);
    if(!get().enabled||!selected?.available){set({formError:selected?.reason||'主图详情生成尚未开放'});return;}
    set({isTransitioning:true,formError:null});clearSettingsTimer();
    try{
      if(requestId&&get().projectId){
        const existing=await getDetailProject(get().projectId!);
        if(existing?.run_state?.request_id===requestId){set({...applyDraft(existing),isTransitioning:false});schedulePoll();return;}
      }
      await persistSettings();
      const {projectId,projectVersion}=get();if(!projectId||projectVersion===null)throw new Error('项目尚未保存');
      requestId??=crypto.randomUUID();
      const project=await startDetailProject(projectId,projectVersion,requestId);
      if(project)set({...applyDraft(project),isTransitioning:false});
      schedulePoll();
    }catch(error){const failure=toApiRequestError(error);
      if(failure.status&&failure.status>=400&&failure.status<500)requestId=null;
      set({isTransitioning:false,formError:failure.message});
      // An unknown HTTP outcome retains the same request key for a safe retry.
    }
  },
  restart:async()=>{
    if(get().projectId)await archiveDetailProject(get().projectId!);
    get().reset();await get().hydrateDraft();
  },
  reset:()=>{
    lifecycleVersion++;clearSettingsTimer();if(pollTimer)clearTimeout(pollTimer);pollTimer=null;
    get().images.forEach(releasePreview);requestId=null;set({...initialState,form:{...DEFAULT_FORM}});
  },
}));
