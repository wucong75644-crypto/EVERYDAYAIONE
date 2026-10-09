import { afterEach,beforeEach,describe,expect,it,vi } from 'vitest';
import { useDetailPageStore } from '../useDetailPageStore';
import * as api from '../../services/detailProject';
import type { DetailProjectDraft } from '../../types/detailPage';
vi.mock('../../services/detailProject',()=>({getCurrentDetailProject:vi.fn(),getDetailCapabilities:vi.fn(),getDetailProject:vi.fn(),startDetailProject:vi.fn(),archiveDetailProject:vi.fn(),attachDetailImage:vi.fn(),removeDetailImage:vi.fn(),saveDetailSettings:vi.fn()}));
vi.mock('../../services/upload',()=>({uploadImageFile:vi.fn()}));
const draft:DetailProjectDraft={id:'project',version:1,content_type:'default',platform:'taobao',requirement:'发财的感觉',language:'zh-CN',aspect_ratio:'1:1',quality:'1k',image_count:14,prompt_model:'kimi-k3',status:'draft',images:[{id:'image',category:'product',workspace_path:'product.png',sort_order:0,status:'ready',original_url:'product.png',thumbnail_url:null}]};
beforeEach(()=>{
  vi.useFakeTimers();vi.clearAllMocks();useDetailPageStore.getState().reset();
  vi.mocked(api.getCurrentDetailProject).mockResolvedValue(draft);
  vi.mocked(api.getDetailCapabilities).mockResolvedValue({enabled:true,prompt_models:[{id:'kimi-k3',name:'Kimi K3',available:true,reason:null}],image_models:[]});
  vi.mocked(api.saveDetailSettings).mockImplementation(async(_id,version)=>({...draft,version:version+1}));
});
afterEach(()=>{useDetailPageStore.getState().reset();vi.useRealTimers();});
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
  vi.mocked(api.getCurrentDetailProject).mockResolvedValue({...draft,status:'analyzing'});
  vi.mocked(api.getDetailProject).mockResolvedValue({...draft,status:'completed'});
  await useDetailPageStore.getState().hydrateDraft();await vi.advanceTimersByTimeAsync(1500);
  expect(api.getDetailProject).toHaveBeenCalledWith('project');expect(useDetailPageStore.getState().status).toBe('completed');
 });
 it('卸载后忽略迟到的草稿结果',async()=>{
  let resolve!:(value:DetailProjectDraft)=>void;
  vi.mocked(api.getCurrentDetailProject).mockImplementationOnce(()=>new Promise(r=>{resolve=r;}));
  const request=useDetailPageStore.getState().hydrateDraft();useDetailPageStore.getState().reset();resolve(draft);await request;
  expect(useDetailPageStore.getState().projectId).toBeNull();
 });
 it('上传仍沿用9张共同上限和类型验证',async()=>{
  await useDetailPageStore.getState().addImages('product',Array.from({length:10},()=>new File(['x'],'a.png',{type:'image/png'})));
  expect(useDetailPageStore.getState().formError).toContain('9');
  await useDetailPageStore.getState().addImages('reference',[new File(['x'],'a.txt',{type:'text/plain'})]);
  expect(useDetailPageStore.getState().formError).toContain('格式');
 });
 it('工作区图片关联后保存当前编辑，避免上传覆盖输入',async()=>{
  await useDetailPageStore.getState().hydrateDraft();
  useDetailPageStore.getState().updateForm({contentType:'main_image',count:15,requirement:'人工编辑要求'});
  vi.mocked(api.attachDetailImage).mockResolvedValue({...draft,version:2});
  await useDetailPageStore.getState().attachWorkspaceImages('product',['second.png']);
  await vi.advanceTimersByTimeAsync(500);
  expect(api.saveDetailSettings).toHaveBeenCalledWith('project',2,expect.objectContaining({contentType:'main_image',count:15,requirement:'人工编辑要求'}));
 });
});
