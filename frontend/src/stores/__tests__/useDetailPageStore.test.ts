import { afterEach,beforeEach,describe,expect,it,vi } from 'vitest';
import { ApiRequestError } from '../../services/api';
import { useDetailPageStore } from '../useDetailPageStore';
import * as api from '../../services/detailProject';
import * as uploadApi from '../../services/upload';
import type { DetailProjectDraft } from '../../types/detailPage';
vi.mock('../../services/detailProject',()=>({createDetailProject:vi.fn(),listDetailProjects:vi.fn(),refreshDetailTaskStatuses:vi.fn(),getDetailCapabilities:vi.fn(),getDetailProject:vi.fn(),startDetailProject:vi.fn(),archiveDetailProject:vi.fn(),attachDetailImage:vi.fn(),removeDetailImage:vi.fn(),saveDetailSettings:vi.fn()}));
vi.mock('../../services/upload',()=>({uploadImageFile:vi.fn()}));
const draft:DetailProjectDraft={id:'project',version:1,content_type:'default',platform:'taobao',requirement:'发财的感觉',language:'zh-CN',aspect_ratio:'1:1',quality:'1k',image_count:14,prompt_model:'kimi-k3',status:'draft',images:[{id:'image',category:'product',workspace_path:'product.png',sort_order:0,status:'ready',original_url:'product.png',thumbnail_url:null}]};
beforeEach(()=>{
  vi.useFakeTimers();vi.resetAllMocks();localStorage.clear();useDetailPageStore.getState().reset();
  vi.mocked(api.getDetailProject).mockResolvedValue(draft);
  vi.mocked(api.listDetailProjects).mockResolvedValue({items:[{id:'project',title:'产品',created_at:'2026-10-10',content_type:'default',status:'draft',display_status:'draft',expected_count:14,completed_count:0,thumbnail_url:null,stage:null,recovery_waiting:false}],next_cursor:null});
  vi.mocked(api.createDetailProject).mockResolvedValue({...draft,id:'new-project'});
  vi.mocked(api.refreshDetailTaskStatuses).mockResolvedValue([]);
  vi.mocked(api.getDetailCapabilities).mockResolvedValue({enabled:true,prompt_models:[{id:'kimi-k3',name:'Kimi K3',available:true,reason:null}],image_models:[]});
  vi.mocked(api.saveDetailSettings).mockImplementation(async(_id,version)=>({...draft,version:version+1}));
});
afterEach(()=>{useDetailPageStore.getState().reset();vi.unstubAllGlobals();vi.useRealTimers();});
describe('真实页面任务状态',()=>{
 it.each(['completed','failed'])('%s 后重新设置所有参数和素材，图片回执不清空历史',async status=>{
  const old={run_id:'old-run',created_at:'2026-10-09',requirement:'旧要求',groups:[{
   plan_id:'old-plan',kind:'main_images' as const,status:'ready',stage:3,count:1,items:[],tasks:[],
  }]};
  vi.mocked(api.getDetailProject).mockResolvedValue({...draft,status,version:4,runs:[old],groups:old.groups,run_state:{run_id:'old-run'}});
  await useDetailPageStore.getState().hydrateDraft();
  const nextForm={contentType:'detail_page' as const,platform:'jd' as const,language:'none' as const,
   aspectRatio:'3:4',quality:'2k' as const,count:3,promptModel:'kimi-k3' as const,requirement:'新的商品和背景'};
  useDetailPageStore.getState().updateForm(nextForm);
  expect(useDetailPageStore.getState().form).toEqual(nextForm);
  vi.mocked(api.removeDetailImage).mockResolvedValue({...draft,status,version:6,images:[]});
  await useDetailPageStore.getState().removeImage('image');
  expect(api.saveDetailSettings).toHaveBeenCalledWith('project',4,nextForm);
  expect(api.removeDetailImage).toHaveBeenCalledWith('project','image',5);
  expect(useDetailPageStore.getState()).toMatchObject({status,images:[],runs:[old],groups:old.groups,currentRunId:'old-run',form:nextForm});
  const replacement={...draft.images[0],id:'new-image',workspace_path:'new-product.png'};
  vi.mocked(api.attachDetailImage).mockResolvedValue({...draft,status,version:8,images:[replacement]});
  await useDetailPageStore.getState().attachWorkspaceImages('product',['new-product.png']);
  expect(api.attachDetailImage).toHaveBeenCalledWith('new-product.png','product','project');
  expect(useDetailPageStore.getState()).toMatchObject({status,images:[{workspacePath:'new-product.png'}],runs:[old],groups:old.groups,currentRunId:'old-run',form:nextForm});
  vi.mocked(api.startDetailProject).mockImplementation(async(_id,_version,request)=>({...draft,status:'analyzing',runs:[old],run_state:{run_id:request},images:[replacement]}));
  await useDetailPageStore.getState().startAnalysis();
  expect(api.startDetailProject).toHaveBeenCalledWith('project',9,expect.any(String));
  expect(useDetailPageStore.getState().runs).toEqual([old]);
 });
 it('已结束任务仍能直接上传文件，全部关联完成前不受理下一轮',async()=>{
  const old={run_id:'old-run',created_at:'2026-10-09',requirement:'旧要求',groups:[]};
  vi.mocked(api.getDetailProject).mockResolvedValue({...draft,status:'completed',runs:[old],run_state:{run_id:'old-run'}});
  await useDetailPageStore.getState().hydrateDraft();
  vi.stubGlobal('URL',Object.assign(class extends URL {},{createObjectURL:vi.fn(()=> 'blob:test'),revokeObjectURL:vi.fn()}));
  let finish!:(value:Awaited<ReturnType<typeof uploadApi.uploadImageFile>>)=>void;
  vi.mocked(uploadApi.uploadImageFile).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
  vi.mocked(api.attachDetailImage).mockResolvedValue({...draft,status:'completed',version:2,images:[...draft.images,{...draft.images[0],id:'new-image',workspace_path:'new.png',sort_order:1}]});
  const pending=useDetailPageStore.getState().addImages('product',[new File(['x'],'new.png',{type:'image/png'})]);
  await vi.advanceTimersByTimeAsync(0);
  useDetailPageStore.getState().updateForm({requirement:'上传中补充文字'});
  vi.mocked(api.startDetailProject).mockRejectedValueOnce(new ApiRequestError('UNAVAILABLE','暂不可用',503));
  const starting=useDetailPageStore.getState().startAnalysis();
  expect(api.startDetailProject).not.toHaveBeenCalled();
  finish({workspace_path:'new.png'} as Awaited<ReturnType<typeof uploadApi.uploadImageFile>>);await pending;await starting;
  expect(api.attachDetailImage).toHaveBeenCalledWith('new.png','product','project');
  expect(useDetailPageStore.getState()).toMatchObject({runs:[old],currentRunId:'old-run',form:{requirement:'上传中补充文字'},isUploading:false});
  expect(useDetailPageStore.getState().images).toHaveLength(2);
 });
 it.each(['analyzing','plan_ready','generating','archived'])('%s 不能通过 store 修改参数或图片',async status=>{
  await useDetailPageStore.getState().hydrateDraft();useDetailPageStore.setState({status});
  useDetailPageStore.getState().updateForm({contentType:'detail_page',quality:'2k'});
  await useDetailPageStore.getState().removeImage('image');
  await useDetailPageStore.getState().attachWorkspaceImages('product',['new.png']);
  await useDetailPageStore.getState().addImages('product',[new File(['x'],'new.png',{type:'image/png'})]);
  expect(useDetailPageStore.getState().form).toMatchObject({contentType:'default',quality:'1k'});
  expect(api.removeDetailImage).not.toHaveBeenCalled();expect(api.attachDetailImage).not.toHaveBeenCalled();
  expect(uploadApi.uploadImageFile).not.toHaveBeenCalled();
 });
 it('修改已结束任务后保存原文并创建新一轮，保留历史且不复用上一轮请求号',async()=>{
  const old={run_id:'old-run',created_at:'2026-10-09',requirement:'旧要求',groups:[]};
  vi.mocked(api.getDetailProject).mockResolvedValue({...draft,status:'completed',version:4,runs:[old],run_state:{run_id:'old-run',request_id:'old-run'}});
  await useDetailPageStore.getState().hydrateDraft();
  useDetailPageStore.getState().updateForm({requirement:'  新风格\n保持细节  '});
  vi.mocked(api.startDetailProject).mockImplementation(async(_id,_version,request)=>({...draft,status:'analyzing',version:6,
    runs:[old,{...old,run_id:request,requirement:'新要求'}],run_state:{run_id:request,request_id:request}}));
  await useDetailPageStore.getState().startAnalysis();
  expect(api.saveDetailSettings).toHaveBeenCalledWith('project',4,expect.objectContaining({requirement:'  新风格\n保持细节  '}));
  expect(api.startDetailProject).toHaveBeenCalledWith('project',5,expect.not.stringMatching(/^old-run$/));
  expect(useDetailPageStore.getState().runs[0]).toEqual(old);
  const first=vi.mocked(api.startDetailProject).mock.calls[0][2];
  useDetailPageStore.setState({status:'completed'});
  await useDetailPageStore.getState().startAnalysis();
  expect(vi.mocked(api.startDetailProject).mock.calls[1][2]).not.toBe(first);
 });
 it('删除选中任务后切换到已有任务，不删除图片或取消后台任务',async()=>{
  const second={id:'second',title:'另一产品',created_at:'2026-10-09',content_type:'default' as const,status:'completed',display_status:'completed' as const,expected_count:14,completed_count:14,thumbnail_url:null,stage:null,recovery_waiting:false};
  const first=(await api.listDetailProjects()).items[0];
  vi.mocked(api.listDetailProjects).mockResolvedValue({items:[first,second],next_cursor:null});
  await useDetailPageStore.getState().hydrateDraft();
  vi.mocked(api.getDetailProject).mockResolvedValue({...draft,id:'second',status:'completed',requirement:'另一产品要求'});
  vi.mocked(api.archiveDetailProject).mockResolvedValue({} as Awaited<ReturnType<typeof api.archiveDetailProject>>);
  vi.mocked(api.listDetailProjects).mockResolvedValue({items:[second],next_cursor:null});
  await useDetailPageStore.getState().deleteTask('project');
  expect(api.archiveDetailProject).toHaveBeenCalledExactlyOnceWith('project');
  expect(useDetailPageStore.getState()).toMatchObject({projectId:'second',form:{requirement:'另一产品要求'}});
  expect(useDetailPageStore.getState().tasks.map(task=>task.id)).toEqual(['second']);
  expect(api.removeDetailImage).not.toHaveBeenCalled();expect(api.createDetailProject).not.toHaveBeenCalled();
 });
 it('删除最后一个任务后新建空白草稿；拒绝删除时保留当前内容',async()=>{
  await useDetailPageStore.getState().hydrateDraft();
  vi.mocked(api.archiveDetailProject).mockRejectedValueOnce(new Error('任务仍在后台执行，请完成后再删除'));
  await useDetailPageStore.getState().deleteTask('project');
  expect(useDetailPageStore.getState()).toMatchObject({projectId:'project',taskError:'任务仍在后台执行，请完成后再删除'});
  vi.mocked(api.archiveDetailProject).mockResolvedValueOnce({} as Awaited<ReturnType<typeof api.archiveDetailProject>>);
  vi.mocked(api.listDetailProjects).mockResolvedValue({items:[],next_cursor:null});
  await useDetailPageStore.getState().deleteTask('project');
  expect(useDetailPageStore.getState().projectId).toBe('new-project');
  expect(api.createDetailProject).toHaveBeenCalledTimes(1);
 });
 it('删除回调迟到时不覆盖已切换账号的列表与选中项',async()=>{
  await useDetailPageStore.getState().hydrateDraft('userA:personal');
  let finish!:()=>void;
  vi.mocked(api.archiveDetailProject).mockImplementationOnce(()=>new Promise(resolve=>{finish=()=>resolve({} as Awaited<ReturnType<typeof api.archiveDetailProject>>);}));
  const pending=useDetailPageStore.getState().deleteTask('project');
  vi.mocked(api.getDetailProject).mockResolvedValue({...draft,id:'B',requirement:'B产品'});
  await useDetailPageStore.getState().hydrateDraft('userB:personal','B');
  finish();await pending;
  expect(useDetailPageStore.getState()).toMatchObject({scopeKey:'userB:personal',projectId:'B',form:{requirement:'B产品'}});
  expect(api.createDetailProject).not.toHaveBeenCalled();
 });
 it('编辑历史要求后刷新不覆盖文字，也不会吞掉另一窗口的版本冲突',async()=>{
  vi.mocked(api.getDetailProject).mockResolvedValue({...draft,status:'completed',version:3});
  await useDetailPageStore.getState().hydrateDraft();
  useDetailPageStore.getState().updateForm({requirement:'本地新要求'});
  vi.mocked(api.getDetailProject).mockResolvedValue({...draft,status:'completed',version:8,requirement:'其他窗口要求'});
  await useDetailPageStore.getState().refresh();
  expect(useDetailPageStore.getState()).toMatchObject({form:{requirement:'本地新要求'},projectVersion:3});
  await vi.advanceTimersByTimeAsync(500);
  expect(api.saveDetailSettings).toHaveBeenCalledWith('project',3,expect.objectContaining({requirement:'本地新要求'}));
 });
 it('默认14张且默认Kimi，没有模拟规划',()=>{
  expect(useDetailPageStore.getState().form).toMatchObject({contentType:'default',count:14,promptModel:'kimi-k3'});
  expect(useDetailPageStore.getState().groups).toEqual([]);
 });
 it('旧任务保留原模型，新任务使用服务端Kimi默认值',async()=>{
  vi.mocked(api.getDetailProject).mockResolvedValueOnce({...draft,prompt_model:'gemini-3.8-flash'});
  await useDetailPageStore.getState().hydrateDraft();
  expect(useDetailPageStore.getState().form.promptModel).toBe('gemini-3.8-flash');
  vi.mocked(api.createDetailProject).mockResolvedValueOnce({...draft,id:'new-project',prompt_model:'kimi-k3'});
  await useDetailPageStore.getState().createTask();
  expect(useDetailPageStore.getState().form.promptModel).toBe('kimi-k3');
 });
 it('切换单类型7张，默认重新变成14张，保留显式比例',()=>{
  const store=useDetailPageStore.getState();store.updateForm({contentType:'detail_page',aspectRatio:'16:9'});
  expect(useDetailPageStore.getState().form).toMatchObject({count:7,aspectRatio:'16:9'});
  store.updateForm({contentType:'default'});expect(useDetailPageStore.getState().form.count).toBe(14);
 });
 it('开始前等待设置保存，使用最新草稿版本和原文',async()=>{
  await useDetailPageStore.getState().hydrateDraft();
  useDetailPageStore.getState().updateForm({requirement:'  发财\n不改文字  '});
  vi.mocked(api.startDetailProject).mockResolvedValue({...draft,version:3,status:'analyzing',groups:[]});
  await useDetailPageStore.getState().startAnalysis();
  expect(api.saveDetailSettings).toHaveBeenCalledWith('project',1,expect.objectContaining({requirement:'  发财\n不改文字  '}));
  expect(api.startDetailProject).toHaveBeenCalledWith('project',2,expect.any(String));
  expect(useDetailPageStore.getState().groups).toEqual([]);
  expect(useDetailPageStore.getState().status).toBe('analyzing');
 });
 it('从旧Gemini任务选择Kimi后，先保存Kimi再按新版本开始',async()=>{
  vi.mocked(api.getDetailProject).mockResolvedValueOnce({...draft,prompt_model:'gemini-3.8-flash'});
  await useDetailPageStore.getState().hydrateDraft();
  useDetailPageStore.getState().updateForm({promptModel:'kimi-k3'});
  vi.mocked(api.startDetailProject).mockResolvedValue({...draft,version:3,status:'analyzing',groups:[]});
  await useDetailPageStore.getState().startAnalysis();
  expect(api.saveDetailSettings).toHaveBeenCalledWith('project',1,expect.objectContaining({promptModel:'kimi-k3'}));
  expect(api.startDetailProject).toHaveBeenCalledWith('project',2,expect.any(String));
  expect(vi.mocked(api.saveDetailSettings).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(api.startDetailProject).mock.invocationCallOrder[0]);
 });
 it('模型不可用时明确报错，不回退模型和伪造进度',async()=>{
  vi.mocked(api.getDetailCapabilities).mockResolvedValue({enabled:true,prompt_models:[{id:'kimi-k3',name:'Kimi',available:false,reason:'模型积分费率未配置'}],image_models:[]});
  await useDetailPageStore.getState().hydrateDraft();await useDetailPageStore.getState().startAnalysis();
  expect(api.startDetailProject).not.toHaveBeenCalled();expect(useDetailPageStore.getState().formError).toBe('模型积分费率未配置');
 });
 it('恢复正在执行的项目并从服务器刷新进度',async()=>{
  vi.mocked(api.getDetailProject).mockResolvedValueOnce({...draft,status:'analyzing'});
  vi.mocked(api.getDetailProject).mockResolvedValue({...draft,status:'completed'});
  await useDetailPageStore.getState().hydrateDraft();await vi.advanceTimersByTimeAsync(1500);
  expect(api.getDetailProject).toHaveBeenCalledWith('project');expect(useDetailPageStore.getState().status).toBe('completed');
 });
 it('卸载后忽略迟到的草稿结果',async()=>{
  let resolve!:(value:DetailProjectDraft)=>void;
  vi.mocked(api.getDetailProject).mockImplementationOnce(()=>new Promise(r=>{resolve=r;}));
  const request=useDetailPageStore.getState().hydrateDraft();await vi.advanceTimersByTimeAsync(0);useDetailPageStore.getState().reset();resolve(draft);await request;
  expect(useDetailPageStore.getState().projectId).toBeNull();
 });
 it('上传仍沿用9张共同上限和类型验证',async()=>{
  await useDetailPageStore.getState().hydrateDraft();
  await useDetailPageStore.getState().addImages('product',Array.from({length:10},()=>new File(['x'],'a.png',{type:'image/png'})));
  expect(useDetailPageStore.getState().formError).toContain('9');
  await useDetailPageStore.getState().addImages('reference',[new File(['x'],'a.txt',{type:'text/plain'})]);
  expect(useDetailPageStore.getState().formError).toContain('格式');
 });
 it('上传期间编辑文字不会被关联响应覆盖，完成后保存最新文字',async()=>{
  await useDetailPageStore.getState().hydrateDraft();
  useDetailPageStore.getState().updateForm({contentType:'main_image',count:15,requirement:'人工编辑要求'});
  let finishAttach!:(value:DetailProjectDraft)=>void;
  vi.mocked(api.attachDetailImage).mockImplementationOnce(()=>new Promise(resolve=>{finishAttach=resolve;}));
  const upload=useDetailPageStore.getState().attachWorkspaceImages('product',['second.png']);
  expect(useDetailPageStore.getState().isUploading).toBe(true);
  await vi.advanceTimersByTimeAsync(0);
  useDetailPageStore.getState().updateForm({requirement:'上传期间补充的尺寸和风格'});
  await vi.advanceTimersByTimeAsync(500);
  expect(api.saveDetailSettings).toHaveBeenCalledTimes(1);
  vi.mocked(api.startDetailProject).mockRejectedValueOnce(new ApiRequestError('UNAVAILABLE','暂不可用',503));
  const starting=useDetailPageStore.getState().startAnalysis();
  expect(api.startDetailProject).not.toHaveBeenCalled();
  finishAttach({...draft,version:2});await upload;await starting;
  expect(useDetailPageStore.getState().form.requirement).toBe('上传期间补充的尺寸和风格');
  await vi.advanceTimersByTimeAsync(500);
  expect(api.saveDetailSettings).toHaveBeenCalledWith('project',2,expect.objectContaining({contentType:'main_image',count:15,requirement:'上传期间补充的尺寸和风格'}));
 });
});

describe('多任务保存和异步隔离',()=>{
 it('上传前保存失败不发出图片请求，保留文字且本地图片可移除',async()=>{
  await useDetailPageStore.getState().hydrateDraft();
  useDetailPageStore.getState().updateForm({requirement:'尚未保存的尺寸和背景'});
  vi.mocked(api.saveDetailSettings).mockRejectedValue(new Error('保存失败'));
  await useDetailPageStore.getState().attachWorkspaceImages('reference',['reference.png']);
  expect(api.attachDetailImage).not.toHaveBeenCalled();
  expect(useDetailPageStore.getState()).toMatchObject({isUploading:false,formError:'保存失败',form:{requirement:'尚未保存的尺寸和背景'}});
  vi.stubGlobal('URL',Object.assign(class extends URL {},{createObjectURL:vi.fn(()=> 'blob:test'),revokeObjectURL:vi.fn()}));
  await useDetailPageStore.getState().addImages('product',[new File(['x'],'new.png',{type:'image/png'})]);
  expect(uploadApi.uploadImageFile).not.toHaveBeenCalled();
  const image=useDetailPageStore.getState().images.find(item=>item.name==='new.png')!;
  expect(image.status).toBe('failed');
  await useDetailPageStore.getState().removeImage(image.id);
  expect(useDetailPageStore.getState().images.map(item=>item.id)).toEqual(['image']);
  expect(useDetailPageStore.getState().form.requirement).toBe('尚未保存的尺寸和背景');
 });
 it('新建保留旧任务且不归档，重复点击只提交一次创建',async()=>{
  await useDetailPageStore.getState().hydrateDraft();
  useDetailPageStore.getState().updateForm({requirement:'保留的产品细节'});
  let finish!:(p:DetailProjectDraft)=>void;
  vi.mocked(api.createDetailProject).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
  const first=useDetailPageStore.getState().createTask();
  await useDetailPageStore.getState().createTask();await vi.advanceTimersByTimeAsync(0);
  expect(api.createDetailProject).toHaveBeenCalledTimes(1);
  expect(api.saveDetailSettings).toHaveBeenCalledWith('project',1,expect.objectContaining({requirement:'保留的产品细节'}));
  finish({...draft,id:'new',requirement:'',images:[]});await first;
  expect(useDetailPageStore.getState().projectId).toBe('new');
  expect(api.archiveDetailProject).not.toHaveBeenCalled();
 });
 it('保存失败保留原草稿和文字，重试成功后才切换',async()=>{
  await useDetailPageStore.getState().hydrateDraft();
  useDetailPageStore.getState().updateForm({requirement:'不能丢失的文字'});
  vi.mocked(api.saveDetailSettings).mockRejectedValueOnce(new Error('保存失败'));
  await useDetailPageStore.getState().selectTask('B');
  expect(useDetailPageStore.getState()).toMatchObject({projectId:'project',formError:'保存失败',isTransitioning:false});
  expect(useDetailPageStore.getState().form.requirement).toBe('不能丢失的文字');
  expect(api.getDetailProject).not.toHaveBeenCalledWith('B');
  vi.mocked(api.getDetailProject).mockResolvedValue({...draft,id:'B',requirement:'B文字'});
  await useDetailPageStore.getState().selectTask('B');
  expect(useDetailPageStore.getState()).toMatchObject({projectId:'B',form:{requirement:'B文字'}});
 });
 it('上传明确绑定原项目，上传期间不允许新建或切换',async()=>{
  await useDetailPageStore.getState().hydrateDraft();
  let finish!:(p:DetailProjectDraft)=>void;
  vi.mocked(api.attachDetailImage).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
  const pending=useDetailPageStore.getState().attachWorkspaceImages('product',['A.png']);
  await vi.advanceTimersByTimeAsync(0);
  await useDetailPageStore.getState().createTask();await useDetailPageStore.getState().selectTask('B');
  expect(api.createDetailProject).not.toHaveBeenCalled();expect(api.getDetailProject).not.toHaveBeenCalledWith('B');
  expect(api.attachDetailImage).toHaveBeenCalledWith('A.png','product','project');
  useDetailPageStore.getState().updateForm({requirement:'上传中可输入'});
  finish({...draft,version:2});await pending;
  expect(useDetailPageStore.getState().form.requirement).toBe('上传中可输入');
 });
 it('账号切换后忽略旧删除结果',async()=>{
  await useDetailPageStore.getState().hydrateDraft('userA:personal');
  let finish!:(p:DetailProjectDraft)=>void;
  vi.mocked(api.removeDetailImage).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
  const pending=useDetailPageStore.getState().removeImage('image');await vi.advanceTimersByTimeAsync(0);
  vi.mocked(api.getDetailProject).mockResolvedValue({...draft,id:'B',requirement:'B资料'});
  await useDetailPageStore.getState().hydrateDraft('userB:personal','B');
  finish({...draft,images:[]});await pending;
  expect(useDetailPageStore.getState()).toMatchObject({projectId:'B',scopeKey:'userB:personal',form:{requirement:'B资料'}});
 });
 it('账号切换后忽略旧分析失败回调',async()=>{
  await useDetailPageStore.getState().hydrateDraft('userA:orgA');
  let fail!:(e:Error)=>void;
  vi.mocked(api.startDetailProject).mockImplementationOnce(()=>new Promise((_resolve,reject)=>{fail=reject;}));
  const pending=useDetailPageStore.getState().startAnalysis();await vi.advanceTimersByTimeAsync(0);
  vi.mocked(api.getDetailProject).mockResolvedValue({...draft,id:'B',requirement:'B资料'});
  await useDetailPageStore.getState().hydrateDraft('userB:orgB','B');
  fail(new Error('A调用失败'));await pending;
  expect(useDetailPageStore.getState()).toMatchObject({projectId:'B',formError:null,isTransitioning:false});
 });
 it('旧自动保存的失败不能覆盖新账号错误状态',async()=>{
  await useDetailPageStore.getState().hydrateDraft('A');
  let fail!:(e:Error)=>void;
  vi.mocked(api.saveDetailSettings).mockImplementationOnce(()=>new Promise((_r,reject)=>{fail=reject;}));
  useDetailPageStore.getState().updateForm({requirement:'A文字'});await vi.advanceTimersByTimeAsync(500);
  vi.mocked(api.getDetailProject).mockResolvedValue({...draft,id:'B'});
  await useDetailPageStore.getState().hydrateDraft('B','B');fail(new Error('A保存失败'));await vi.advanceTimersByTimeAsync(0);
  expect(useDetailPageStore.getState()).toMatchObject({projectId:'B',formError:null});
 });
 it('创建回执丢失后用同一个请求编号恢复',async()=>{
  await useDetailPageStore.getState().hydrateDraft();
  vi.mocked(api.createDetailProject).mockRejectedValueOnce(new Error('网络中断'));
  await useDetailPageStore.getState().createTask();await useDetailPageStore.getState().createTask();
  expect(vi.mocked(api.createDetailProject).mock.calls[0][0]).toBe(vi.mocked(api.createDetailProject).mock.calls[1][0]);
 });
 it('选择记忆按用户和组织分别保存，URL任务优先',async()=>{
  await useDetailPageStore.getState().hydrateDraft('A:org1','project');
  vi.mocked(api.getDetailProject).mockResolvedValue({...draft,id:'B'});
  await useDetailPageStore.getState().hydrateDraft('A:org2','B');
  expect(localStorage.getItem('detail-task:A:org1')).toBe('project');expect(localStorage.getItem('detail-task:A:org2')).toBe('B');
  vi.mocked(api.getDetailProject).mockResolvedValue(draft);
  await useDetailPageStore.getState().hydrateDraft('A:org1');
  expect(api.getDetailProject).toHaveBeenLastCalledWith('project');
 });
 it('历史分页中的运行任务通过批量状态接口刷新，列表顺序不跳动',async()=>{
  await useDetailPageStore.getState().hydrateDraft();
  const first=useDetailPageStore.getState().tasks[0];
  useDetailPageStore.setState({tasks:[first,{...first,id:'older',created_at:'2026-10-09',status:'generating',display_status:'generating'}]});
  vi.mocked(api.refreshDetailTaskStatuses).mockResolvedValue([{...first,id:'older',created_at:'2026-10-09',status:'completed',display_status:'completed',completed_count:14}]);
  await useDetailPageStore.getState().refreshTasks();
  expect(api.refreshDetailTaskStatuses).toHaveBeenCalledWith(['older']);
  expect(useDetailPageStore.getState().tasks.map(t=>[t.id,t.completed_count])).toEqual([['project',0],['older',14]]);
 });
});

it('受理回执为空时保留同一请求编号，可再次核实而不会卡在提交中',async()=>{
 await useDetailPageStore.getState().hydrateDraft();
 vi.mocked(api.startDetailProject).mockResolvedValueOnce(null).mockResolvedValue({...draft,status:'analyzing'});
 await useDetailPageStore.getState().startAnalysis();
 expect(useDetailPageStore.getState().isTransitioning).toBe(false);
 expect(useDetailPageStore.getState().formError).toContain('尚未确认');
 await useDetailPageStore.getState().startAnalysis();
 expect(vi.mocked(api.startDetailProject).mock.calls[0][2]).toBe(vi.mocked(api.startDetailProject).mock.calls[1][2]);
});

describe('并发刷新与异常恢复',()=>{
 it('同一任务的旧进度晚到不能覆盖已完成状态或重新启动轮询',async()=>{
  await useDetailPageStore.getState().hydrateDraft();await vi.advanceTimersByTimeAsync(0);
  let finish!:(value:DetailProjectDraft)=>void;
  vi.mocked(api.getDetailProject).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
  const older=useDetailPageStore.getState().refresh();
  vi.mocked(api.getDetailProject).mockResolvedValueOnce({...draft,version:5,status:'completed'});
  await useDetailPageStore.getState().refresh();
  finish({...draft,version:2,status:'generating'});await older;
  expect(useDetailPageStore.getState()).toMatchObject({status:'completed',projectVersion:5});
  const calls=vi.mocked(api.getDetailProject).mock.calls.length;
  await vi.advanceTimersByTimeAsync(1600);
  expect(api.getDetailProject).toHaveBeenCalledTimes(calls);
 });
 it('旧刷新失败不能覆盖较新成功的状态',async()=>{
  await useDetailPageStore.getState().hydrateDraft();
  let fail!:(error:Error)=>void;
  vi.mocked(api.getDetailProject).mockImplementationOnce(()=>new Promise((_resolve,reject)=>{fail=reject;}));
  const older=useDetailPageStore.getState().refresh();
  vi.mocked(api.getDetailProject).mockResolvedValueOnce({...draft,status:'generating'});
  await useDetailPageStore.getState().refresh();fail(new Error('旧请求失败'));await older;
  expect(useDetailPageStore.getState()).toMatchObject({status:'generating',formError:null});
 });
 it('任务列表旧响应不能把已完成任务退回生成中',async()=>{
  await useDetailPageStore.getState().hydrateDraft();await vi.advanceTimersByTimeAsync(0);
  const item=useDetailPageStore.getState().tasks[0];
  let finish!:(value:Awaited<ReturnType<typeof api.listDetailProjects>>)=>void;
  vi.mocked(api.listDetailProjects).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
  const older=useDetailPageStore.getState().refreshTasks();
  vi.mocked(api.listDetailProjects).mockResolvedValueOnce({items:[{...item,status:'completed',display_status:'completed',completed_count:14}],next_cursor:null});
  await useDetailPageStore.getState().refreshTasks();
  finish({items:[{...item,status:'generating',display_status:'generating',completed_count:1}],next_cursor:null});await older;
  expect(useDetailPageStore.getState().tasks[0]).toMatchObject({status:'completed',completed_count:14});
 });
 it('进度和列表请求失败后持续轮询，恢复网络后完成',async()=>{
  vi.mocked(api.getDetailProject).mockResolvedValueOnce({...draft,status:'generating'});
  await useDetailPageStore.getState().hydrateDraft();await vi.advanceTimersByTimeAsync(0);
  vi.mocked(api.getDetailProject).mockRejectedValueOnce(new Error('网络中断')).mockResolvedValue({...draft,status:'completed'});
  const item=useDetailPageStore.getState().tasks[0];
  useDetailPageStore.setState({tasks:[{...item,status:'generating',display_status:'generating'}]});
  vi.mocked(api.refreshDetailTaskStatuses).mockRejectedValueOnce(new Error('列表网络中断'));
  await vi.advanceTimersByTimeAsync(1500);
  expect(useDetailPageStore.getState()).toMatchObject({status:'generating',formError:'网络中断'});
  await vi.advanceTimersByTimeAsync(1500);
  expect(useDetailPageStore.getState()).toMatchObject({status:'completed',formError:null});
  await vi.advanceTimersByTimeAsync(2000);
  expect(useDetailPageStore.getState().taskError).toBe('列表网络中断');
  await vi.advanceTimersByTimeAsync(5000);
  expect(useDetailPageStore.getState().taskError).toBeNull();
 });
 it('切换到另一后台任务后忽略原任务的迟到结果',async()=>{
  vi.mocked(api.getDetailProject).mockResolvedValueOnce({...draft,status:'generating'});
  await useDetailPageStore.getState().hydrateDraft();
  let finish!:(value:DetailProjectDraft)=>void;
  vi.mocked(api.getDetailProject).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
  const pending=useDetailPageStore.getState().refresh();
  vi.mocked(api.getDetailProject).mockResolvedValueOnce({...draft,id:'B',status:'analyzing',requirement:'B产品'});
  await useDetailPageStore.getState().selectTask('B');
  finish({...draft,status:'completed'});await pending;
  expect(useDetailPageStore.getState()).toMatchObject({projectId:'B',status:'analyzing',form:{requirement:'B产品'}});
 });
 it('同一任务反复点击开始只发起一次受理，回执丢失后核实原请求',async()=>{
  await useDetailPageStore.getState().hydrateDraft();
  let fail!:(error:Error)=>void;
  vi.mocked(api.startDetailProject).mockImplementationOnce(()=>new Promise((_resolve,reject)=>{fail=reject;}));
  const pending=useDetailPageStore.getState().startAnalysis();await vi.advanceTimersByTimeAsync(0);
  await useDetailPageStore.getState().startAnalysis();
  expect(api.startDetailProject).toHaveBeenCalledTimes(1);
  const request=vi.mocked(api.startDetailProject).mock.calls[0][2];
  fail(new Error('受理回执丢失'));await pending;
  vi.mocked(api.getDetailProject).mockResolvedValueOnce({...draft,status:'generating',run_state:{run_id:request,request_id:request}});
  await useDetailPageStore.getState().startAnalysis();
  expect(api.startDetailProject).toHaveBeenCalledTimes(1);
  expect(useDetailPageStore.getState()).toMatchObject({status:'generating',isTransitioning:false});
 });
});


describe('轻量后台刷新',()=>{
 it('首次打开只读取一次列表，定时仅批量刷新运行任务并保留分页位置',async()=>{
  await useDetailPageStore.getState().hydrateDraft();
  expect(api.listDetailProjects).toHaveBeenCalledTimes(1);
  const first=useDetailPageStore.getState().tasks[0];
  useDetailPageStore.setState({tasks:[first,{...first,id:'running',status:'generating',display_status:'generating'}],taskCursor:'cursor'});
  vi.mocked(api.refreshDetailTaskStatuses).mockResolvedValue([{...first,id:'running',status:'completed',display_status:'completed',completed_count:14}]);
  await vi.advanceTimersByTimeAsync(5000);
  expect(api.listDetailProjects).toHaveBeenCalledTimes(1);
  expect(api.refreshDetailTaskStatuses).toHaveBeenCalledExactlyOnceWith(['running']);
  expect(useDetailPageStore.getState().taskCursor).toBe('cursor');
  await vi.advanceTimersByTimeAsync(5000);
  expect(api.refreshDetailTaskStatuses).toHaveBeenCalledTimes(1);
  await useDetailPageStore.getState().refreshTasks();
  expect(api.listDetailProjects).toHaveBeenCalledTimes(2);
 });
 it('定期重新读取列表以发现其他窗口的新任务，空列表创建的任务也会立即出现',async()=>{
  await useDetailPageStore.getState().hydrateDraft();
  const item=useDetailPageStore.getState().tasks[0];
  vi.mocked(api.listDetailProjects).mockResolvedValue({items:[{...item,id:'other'},item],next_cursor:null});
  await vi.advanceTimersByTimeAsync(30000);
  expect(api.listDetailProjects).toHaveBeenCalledTimes(2);
  expect(useDetailPageStore.getState().tasks.map(t=>t.id)).toContain('other');
  vi.mocked(api.listDetailProjects).mockResolvedValueOnce({items:[],next_cursor:null})
   .mockResolvedValueOnce({items:[{...item,id:'new-project'}],next_cursor:null});
  await useDetailPageStore.getState().hydrateDraft('new-user');await vi.advanceTimersByTimeAsync(0);
  expect(useDetailPageStore.getState().tasks.map(t=>t.id)).toEqual(['new-project']);
 });
 it('手动详情请求未完成时定时刷新不重复请求，完成后继续轮询',async()=>{
  vi.mocked(api.getDetailProject).mockResolvedValueOnce({...draft,status:'generating'});
  await useDetailPageStore.getState().hydrateDraft();
  let finish!:(p:DetailProjectDraft)=>void;
  vi.mocked(api.getDetailProject).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
  const pending=useDetailPageStore.getState().refresh();
  await vi.advanceTimersByTimeAsync(1500);
  expect(api.getDetailProject).toHaveBeenCalledTimes(2);
  finish({...draft,status:'generating'});await pending;
  await vi.advanceTimersByTimeAsync(1500);
  expect(api.getDetailProject).toHaveBeenCalledTimes(3);
 });
 it('列表请求未完成时定时刷新不另发批量状态请求',async()=>{
  await useDetailPageStore.getState().hydrateDraft();
  const item=useDetailPageStore.getState().tasks[0];
  useDetailPageStore.setState({tasks:[{...item,status:'generating',display_status:'generating'}]});
  let finish!:(p:Awaited<ReturnType<typeof api.listDetailProjects>>)=>void;
  vi.mocked(api.listDetailProjects).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
  const pending=useDetailPageStore.getState().refreshTasks();
  await vi.advanceTimersByTimeAsync(5000);
  expect(api.refreshDetailTaskStatuses).not.toHaveBeenCalled();
  finish({items:[{...item,status:'completed',display_status:'completed'}],next_cursor:null});await pending;
  expect(useDetailPageStore.getState().tasks[0].status).toBe('completed');
 });
 it('切换期间暂停自动请求，旧请求挂起不会阻塞新任务的后台刷新',async()=>{
  vi.mocked(api.getDetailProject).mockResolvedValueOnce({...draft,status:'generating'});
  await useDetailPageStore.getState().hydrateDraft();
  let finishOld!:(p:DetailProjectDraft)=>void;
  vi.mocked(api.getDetailProject).mockImplementationOnce(()=>new Promise(resolve=>{finishOld=resolve;}));
  const older=useDetailPageStore.getState().refresh();
  let finishNew!:(p:DetailProjectDraft)=>void;
  vi.mocked(api.getDetailProject).mockImplementationOnce(()=>new Promise(resolve=>{finishNew=resolve;}));
  const switching=useDetailPageStore.getState().selectTask('B');
  await vi.advanceTimersByTimeAsync(5000);
  expect(api.getDetailProject).toHaveBeenCalledTimes(3);
  finishNew({...draft,id:'B',status:'generating'});await switching;
  vi.mocked(api.getDetailProject).mockResolvedValueOnce({...draft,id:'B',status:'completed'});
  await vi.advanceTimersByTimeAsync(1500);
  expect(useDetailPageStore.getState()).toMatchObject({projectId:'B',status:'completed'});
  finishOld({...draft,status:'generating'});await older;
  expect(useDetailPageStore.getState()).toMatchObject({projectId:'B',status:'completed'});
 });
});


describe('乐观删除与项目写入顺序',()=>{
 const second={...draft.images[0],id:'second',workspace_path:'second.png',sort_order:1};
 const timeout=()=>new ApiRequestError('REQUEST_TIMEOUT','请求超时',undefined,undefined,'timeout');
 it('连续删除立即隐藏，旧回执不复活图片，明确失败只恢复对应项和位置',async()=>{
  vi.mocked(api.getDetailProject).mockResolvedValue({...draft,images:[draft.images[0],second]});
  await useDetailPageStore.getState().hydrateDraft();
  let finish!:(p:DetailProjectDraft)=>void;
  vi.mocked(api.removeDetailImage).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}))
   .mockRejectedValueOnce(new ApiRequestError('DENIED','禁止删除',403));
  const first=useDetailPageStore.getState().removeImage('image');
  const next=useDetailPageStore.getState().removeImage('second');
  expect(useDetailPageStore.getState().images).toEqual([]);
  useDetailPageStore.getState().updateForm({requirement:'连续删除时的编辑',quality:'2k'});
  await vi.advanceTimersByTimeAsync(0);
  expect(api.removeDetailImage).toHaveBeenCalledTimes(1);
  const version=vi.mocked(api.removeDetailImage).mock.calls[0][2];
  finish({...draft,version:version+1,images:[second]});await first;await next;
  expect(api.removeDetailImage).toHaveBeenLastCalledWith('project','second',version+1);
  expect(useDetailPageStore.getState().images.map(i=>i.id)).toEqual(['second']);
  expect(useDetailPageStore.getState().form).toMatchObject({requirement:'连续删除时的编辑',quality:'2k'});
  expect(useDetailPageStore.getState()).toMatchObject({isMutating:false,pendingDeleteIds:[],formError:'禁止删除'});
 });
 it('删除中保存和工作区关联排队，使用删除确认后的版本',async()=>{
  await useDetailPageStore.getState().hydrateDraft();
  let finish!:(p:DetailProjectDraft)=>void;
  vi.mocked(api.removeDetailImage).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
  const deletion=useDetailPageStore.getState().removeImage('image');await vi.advanceTimersByTimeAsync(0);
  useDetailPageStore.getState().updateForm({requirement:'图片删除中的编辑'});
  vi.mocked(api.attachDetailImage).mockResolvedValue({...draft,version:4,images:[second]});
  const attaching=useDetailPageStore.getState().attachWorkspaceImages('product',['second.png']);
  await vi.advanceTimersByTimeAsync(500);
  expect(api.attachDetailImage).not.toHaveBeenCalled();expect(api.saveDetailSettings).not.toHaveBeenCalled();
  finish({...draft,version:2,images:[]});await deletion;await attaching;
  expect(api.saveDetailSettings).toHaveBeenCalledWith('project',2,expect.objectContaining({requirement:'图片删除中的编辑'}));
  expect(useDetailPageStore.getState().images.map(i=>i.id)).toEqual(['second']);
  expect(useDetailPageStore.getState().form.requirement).toBe('图片删除中的编辑');
 });
 it('超时后查询确认已删除，直接收敛为成功',async()=>{
  await useDetailPageStore.getState().hydrateDraft();
  vi.mocked(api.removeDetailImage).mockRejectedValueOnce(timeout());
  vi.mocked(api.getDetailProject).mockResolvedValueOnce({...draft,version:2,images:[]});
  await useDetailPageStore.getState().removeImage('image');
  expect(useDetailPageStore.getState()).toMatchObject({images:[],pendingDeleteIds:[],isMutating:false,syncUncertain:false,projectVersion:2,formError:null});
 });
 it('超时后仍读到图片不认定失败，暂停后续写入，重新确认后恢复保存',async()=>{
  await useDetailPageStore.getState().hydrateDraft();
  vi.mocked(api.removeDetailImage).mockRejectedValueOnce(timeout());
  await useDetailPageStore.getState().removeImage('image');
  expect(useDetailPageStore.getState()).toMatchObject({images:[],pendingDeleteIds:['image'],isMutating:true,syncUncertain:true});
  useDetailPageStore.getState().updateForm({requirement:'结果不确定时仍可编辑'});await vi.advanceTimersByTimeAsync(500);
  await useDetailPageStore.getState().startAnalysis();
  await useDetailPageStore.getState().attachWorkspaceImages('product',['new.png']);
  expect(api.saveDetailSettings).not.toHaveBeenCalled();expect(api.startDetailProject).not.toHaveBeenCalled();expect(api.attachDetailImage).not.toHaveBeenCalled();
  vi.mocked(api.getDetailProject).mockResolvedValueOnce({...draft,version:2,images:[]});
  await useDetailPageStore.getState().confirmImageSync();await vi.advanceTimersByTimeAsync(500);
  expect(useDetailPageStore.getState()).toMatchObject({syncUncertain:false,isMutating:false,isConfirmingSync:false,form:{requirement:'结果不确定时仍可编辑'}});
  expect(api.saveDetailSettings).toHaveBeenCalledWith('project',2,expect.objectContaining({requirement:'结果不确定时仍可编辑'}));
 });
 it('结果不确定时重新确认按读取的新版本重试，不盲目恢复图片',async()=>{
  await useDetailPageStore.getState().hydrateDraft();
  vi.mocked(api.removeDetailImage).mockRejectedValueOnce(timeout());
  await useDetailPageStore.getState().removeImage('image');
  vi.mocked(api.getDetailProject).mockResolvedValueOnce({...draft,version:7});
  vi.mocked(api.removeDetailImage).mockResolvedValueOnce({...draft,version:8,images:[]});
  await useDetailPageStore.getState().confirmImageSync();
  expect(api.removeDetailImage).toHaveBeenLastCalledWith('project','image',7);
  expect(useDetailPageStore.getState()).toMatchObject({syncUncertain:false,images:[],projectVersion:8});
 });
 it('重新确认请求失败仍保留待确认状态和编辑内容',async()=>{
  await useDetailPageStore.getState().hydrateDraft();
  vi.mocked(api.removeDetailImage).mockRejectedValueOnce(timeout());
  vi.mocked(api.getDetailProject).mockRejectedValue(new Error('网络不通'));
  await useDetailPageStore.getState().removeImage('image');
  useDetailPageStore.getState().updateForm({requirement:'不能丢失'});
  await useDetailPageStore.getState().confirmImageSync();
  expect(useDetailPageStore.getState()).toMatchObject({syncUncertain:true,isConfirmingSync:false,images:[],form:{requirement:'不能丢失'}});
 });
 it('版本冲突读取最新项目后重试，不覆盖本地表单',async()=>{
  await useDetailPageStore.getState().hydrateDraft();
  vi.mocked(api.removeDetailImage).mockRejectedValueOnce(new ApiRequestError('DETAIL_PROJECT_VERSION_CONFLICT','版本冲突',409))
   .mockResolvedValueOnce({...draft,version:8,images:[]});
  vi.mocked(api.getDetailProject).mockResolvedValueOnce({...draft,version:7});
  await useDetailPageStore.getState().removeImage('image');
  expect(api.removeDetailImage).toHaveBeenLastCalledWith('project','image',7);
  expect(useDetailPageStore.getState()).toMatchObject({images:[],syncUncertain:false});
 });
 it('生成等待删除和最新表单保存完成，只发送一次生成请求',async()=>{
  vi.mocked(api.getDetailProject).mockResolvedValue({...draft,images:[draft.images[0],second]});
  await useDetailPageStore.getState().hydrateDraft();
  let finish!:(p:DetailProjectDraft)=>void;
  vi.mocked(api.removeDetailImage).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
  const deletion=useDetailPageStore.getState().removeImage('image');await vi.advanceTimersByTimeAsync(0);
  useDetailPageStore.getState().updateForm({requirement:'生成必须采用的新要求'});
  vi.mocked(api.startDetailProject).mockResolvedValue({...draft,version:4,status:'analyzing',images:[second]});
  const starting=useDetailPageStore.getState().startAnalysis();
  await useDetailPageStore.getState().startAnalysis();
  expect(api.startDetailProject).not.toHaveBeenCalled();
  finish({...draft,version:2,images:[second]});await deletion;await starting;
  expect(api.saveDetailSettings).toHaveBeenCalledWith('project',2,expect.objectContaining({requirement:'生成必须采用的新要求'}));
  expect(api.startDetailProject).toHaveBeenCalledTimes(1);
  expect(api.startDetailProject).toHaveBeenCalledWith('project',3,expect.any(String));
 });
 it('删除明确失败时取消等待中的生成，不带恢复的图片悄悄提交',async()=>{
  vi.mocked(api.getDetailProject).mockResolvedValue({...draft,images:[draft.images[0],second]});
  await useDetailPageStore.getState().hydrateDraft();
  let reject!:(e:Error)=>void;
  vi.mocked(api.removeDetailImage).mockImplementationOnce(()=>new Promise((_resolve,fail)=>{reject=fail;}));
  const deletion=useDetailPageStore.getState().removeImage('image');await vi.advanceTimersByTimeAsync(0);
  const starting=useDetailPageStore.getState().startAnalysis();
  reject(new ApiRequestError('DENIED','禁止删除',403));await deletion;await starting;
  expect(api.startDetailProject).not.toHaveBeenCalled();
  expect(useDetailPageStore.getState()).toMatchObject({isTransitioning:false,formError:'禁止删除'});
 });
 it('重复点击同一图片只提交一次，确认前不释放恢复所需的预览',async()=>{
  await useDetailPageStore.getState().hydrateDraft();
  const revoke=vi.fn();vi.stubGlobal('URL',Object.assign(class extends URL {},{revokeObjectURL:revoke}));
  useDetailPageStore.setState(state=>({images:state.images.map(image=>({...image,previewUrl:'blob:keep'}))}));
  vi.mocked(api.removeDetailImage).mockRejectedValueOnce(new ApiRequestError('DENIED','禁止删除',403));
  const deletion=useDetailPageStore.getState().removeImage('image');
  await useDetailPageStore.getState().removeImage('image');await deletion;
  expect(api.removeDetailImage).toHaveBeenCalledTimes(1);expect(revoke).not.toHaveBeenCalled();
  expect(useDetailPageStore.getState().images[0].previewUrl).toBe('blob:keep');
 });
});


describe('删除与上传的交叉操作',()=>{
 it('删除期间新上传立即保留本地预览，旧删除响应不清掉上传项',async()=>{
  await useDetailPageStore.getState().hydrateDraft();
  vi.stubGlobal('URL',Object.assign(class extends URL {},{createObjectURL:vi.fn(()=> 'blob:new'),revokeObjectURL:vi.fn()}));
  let finish!:(p:DetailProjectDraft)=>void;
  vi.mocked(api.removeDetailImage).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
  const deletion=useDetailPageStore.getState().removeImage('image');await vi.advanceTimersByTimeAsync(0);
  let uploadFinish!:(p:Awaited<ReturnType<typeof uploadApi.uploadImageFile>>)=>void;
  vi.mocked(uploadApi.uploadImageFile).mockImplementationOnce(()=>new Promise(resolve=>{uploadFinish=resolve;}));
  const uploading=useDetailPageStore.getState().addImages('product',[new File(['x'],'new.png',{type:'image/png'})]);
  expect(useDetailPageStore.getState().images).toMatchObject([{name:'new.png',previewUrl:'blob:new',status:'local'}]);
  finish({...draft,version:2,images:[]});await deletion;await vi.advanceTimersByTimeAsync(0);
  expect(useDetailPageStore.getState().images).toMatchObject([{name:'new.png',previewUrl:'blob:new',status:'uploading'}]);
  vi.mocked(api.attachDetailImage).mockResolvedValue({...draft,version:3,images:[{...draft.images[0],id:'new',workspace_path:'new.png'}]});
  uploadFinish({workspace_path:'new.png'} as Awaited<ReturnType<typeof uploadApi.uploadImageFile>>);await uploading;
  expect(useDetailPageStore.getState().images).toMatchObject([{id:'new',previewUrl:'blob:new',status:'ready'}]);
  expect(useDetailPageStore.getState().images).toHaveLength(1);
 });
 it('其他页面已经移除同一图片时，404经读取核实视为完成',async()=>{
  await useDetailPageStore.getState().hydrateDraft();
  vi.mocked(api.removeDetailImage).mockRejectedValueOnce(new ApiRequestError('DETAIL_IMAGE_NOT_FOUND','图片已移除',404));
  vi.mocked(api.getDetailProject).mockResolvedValueOnce({...draft,version:2,images:[]});
  await useDetailPageStore.getState().removeImage('image');
  expect(useDetailPageStore.getState()).toMatchObject({images:[],pendingDeleteIds:[],syncUncertain:false,formError:null});
 });
});
