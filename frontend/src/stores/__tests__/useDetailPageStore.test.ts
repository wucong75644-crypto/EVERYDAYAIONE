import { afterEach,beforeEach,describe,expect,it,vi } from 'vitest';
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
 it('默认14张且默认Kimi，没有模拟规划',()=>{
  expect(useDetailPageStore.getState().form).toMatchObject({contentType:'default',count:14,promptModel:'kimi-k3'});
  expect(useDetailPageStore.getState().groups).toEqual([]);
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
  await useDetailPageStore.getState().startAnalysis();
  expect(api.startDetailProject).not.toHaveBeenCalled();
  finishAttach({...draft,version:2});await upload;
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
