import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { DetailGroup } from '../../../types/detailPage';
import { chatImageService } from '../../../services/chatImage';
import { DetailWorkspace } from '../DetailWorkspace';
import { downloadWorkspaceZip } from '../../../services/workspace';
import { resumeDetailPlan, stopDetailRecovery } from '../../../services/detailProject';

vi.mock('../../chat/media/ChatImageControls', () => ({ default: ({taskId}:{taskId:string}) => <button>任务详情 {taskId}</button> }));
vi.mock('../../chat/message/MessageImageBlocks', () => ({ AiGeneratedImage: ({renderId,onImageClick}:{renderId:string;onImageClick:()=>void}) => <button onClick={onImageClick}>图片 {renderId}</button> }));
vi.mock('../../../preview/PreviewHost', () => ({default:({state,onIndexChange}:{state:{kind:string;items?:Array<{url:string}>;index?:number};onIndexChange:(i:number)=>void})=>state.kind==='open'?<div role="dialog">{state.items?.[state.index??0].url}<button onClick={()=>onIndexChange((state.index??0)+1)}>下一张</button></div>:null}));
vi.mock('../../chat/media/ImagePreviewModal', () => ({ default: () => null }));
vi.mock('../../chat/media/MediaPlaceholder', () => ({ FailedMediaPlaceholder: ({onRetry,errorMessage}:{onRetry?:()=>void;errorMessage?:string}) => <div>{errorMessage}{onRetry&&<button onClick={onRetry}>重新生成</button>}</div> }));
vi.mock('../../../services/chatImage', () => ({ chatImageService: {replay:vi.fn()} }));
vi.mock('../../../services/workspace', () => ({ downloadWorkspaceZip: vi.fn() }));
vi.mock('../../../services/detailProject', () => ({ resumeDetailPlan: vi.fn(), stopDetailRecovery: vi.fn() }));

const group:DetailGroup = {
  plan_id:'plan-1',kind:'main_images',status:'ready',stage:3,count:2,tasks:[],
  items:[1,2].map(position=>({item_id:`item-${position}`,position,name:`主图${position}`,purpose:'商品展示',request_text:`执行正文${position}`,aspect_ratio:'1:1'})),
};
const tasks:DetailGroup['tasks'] = [2,1].map(position=>({id:`task-${position}`,item_id:`item-${position}`,status:'pending',submission_state:'queued',created_at:'2026-10-09'}));

describe('右侧提示词和图片工作区',()=>{
  it('历史在上，新轮在下；仅新轮变化时定位，旧轮不能重试且统一预览可跨轮切图',()=>{
    const scroll=vi.fn();Element.prototype.scrollIntoView=scroll;
    const old={run_id:'old',created_at:'2026-10-09',requirement:'旧要求',groups:[{...group,status:'failed',can_resume:true,tasks:[
      {...tasks[0],status:'failed'}, {...tasks[1],status:'completed',result_data:{url:'old.png',workspace_path:'old.png'}}]}]};
    const next={run_id:'new',created_at:'2026-10-10',requirement:'新要求',groups:[{...group,plan_id:'new-plan',tasks:[
      {...tasks[1],id:'new-task',status:'completed',result_data:{url:'new.png',workspace_path:'new.png'}}]}]};
    const props={projectId:'project',groups:next.groups,onRefresh:vi.fn(),currentRunId:'new',runs:[old,next]};
    const {rerender}=render(<DetailWorkspace {...props}/>);
    const oldRegion=screen.getByRole('region',{name:'第1轮生成'}),newRegion=screen.getByRole('region',{name:'第2轮生成'});
    expect(oldRegion.compareDocumentPosition(newRegion)&Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(scroll).toHaveBeenCalledTimes(1);
    expect(within(oldRegion).queryByRole('button',{name:'重新生成'})).not.toBeInTheDocument();
    rerender(<DetailWorkspace {...props} runs={[old,{...next}]}/>);expect(scroll).toHaveBeenCalledTimes(1);
    fireEvent.click(within(oldRegion).getByRole('button',{name:'图片 task-1'}));
    expect(screen.getByRole('dialog')).toHaveTextContent('old.png');
    fireEvent.click(screen.getByRole('button',{name:'下一张'}));expect(screen.getByRole('dialog')).toHaveTextContent('new.png');
    fireEvent.click(screen.getByRole('button',{name:'批量下载（2张）'}));
    expect(downloadWorkspaceZip).toHaveBeenCalledWith(['old.png','new.png'],expect.objectContaining({archivePaths:['主图/第01轮/01-主图1.png','主图/第02轮/01-主图1.png']}));
    delete (Element.prototype as Partial<Element>).scrollIntoView;
  });
  beforeEach(()=>vi.clearAllMocks());
  it('重试发现原图变化后显示可操作原因，暂停期间不提供重复生成',()=>{
    render(<DetailWorkspace projectId="project-1" onRefresh={vi.fn()} groups={[{...group,
      tasks:tasks.map(task=>({...task,status:task.id==='task-1'?'failed':'completed'})),
      auto_recovery:{enabled:true,status:'blocked',retry_cost:'platform',items:{
        'item-1':{status:'blocked',attempts:1,message:'原始图片已变化，请重新上传后开始。'}}}}]}/>);
    expect(screen.getByText('原始图片已变化，请重新上传后开始。')).toBeInTheDocument();
    expect(screen.queryByRole('button',{name:'重新生成'})).not.toBeInTheDocument();
    expect(screen.getByText('图片 task-2')).toBeInTheDocument();
  });
  it('自动恢复失败项时保留正常图片和提示词，停止只发送一次请求',async()=>{
    vi.mocked(stopDetailRecovery).mockResolvedValue({} as Awaited<ReturnType<typeof stopDetailRecovery>>);
    const onRefresh=vi.fn();
    const recovery={enabled:true,status:'waiting' as const,retry_cost:'platform' as const,
      items:{'item-1':{status:'waiting' as const,attempts:2}}};
    const props={projectId:'project-1',onRefresh};
    const {rerender}=render(<DetailWorkspace {...props} groups={[{...group,auto_recovery:recovery,
      tasks:tasks.map(task=>({...task,status:task.id==='task-1'?'failed':'completed'}))}]}/>);
    expect(screen.queryByRole('button',{name:'重新生成'})).not.toBeInTheDocument();
    expect(screen.getByText('自动恢复中，正在补齐此图片')).toBeInTheDocument();
    expect(screen.getByText('图片 task-2')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'展开提示词 · 2份'}));
    expect(screen.getByText('执行正文1')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'停止自动重试'}));
    await waitFor(()=>expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(stopDetailRecovery).toHaveBeenCalledExactlyOnceWith('project-1');
    rerender(<DetailWorkspace {...props} groups={[{...group,auto_recovery:null,
      tasks:tasks.map(task=>({...task,status:task.id==='task-1'?'failed':'completed'}))}]}/>);
    expect(screen.queryByRole('button',{name:'停止自动重试'})).not.toBeInTheDocument();
    expect(screen.getByRole('button',{name:'重新生成'})).toBeInTheDocument();
  });
  it('策划临时失败显示恢复动画，隐藏手动重启入口',()=>{
    render(<DetailWorkspace projectId="project-1" onRefresh={vi.fn()} groups={[{...group,status:'failed',stage:2,
      can_resume:true,error:{code:'MODEL_TIMEOUT'},items:[],auto_recovery:{enabled:true,status:'waiting',retry_cost:'platform',items:{}}}]}/>);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByRole('button',{name:'从失败阶段继续'})).not.toBeInTheDocument();
    expect(screen.getByRole('listitem',{name:'视觉定位：正在执行'})).toBeInTheDocument();
    expect(screen.getByRole('button',{name:'停止自动重试'})).toBeInTheDocument();
  });
  it('已保存提示词先可见，受理后折叠，并可在进度更新后继续展开阅读',()=>{
    const props={projectId:'project-1',onRefresh:vi.fn()};
    const {rerender}=render(<DetailWorkspace {...props} groups={[group]}/>);
    expect(screen.getByText('执行正文1')).toBeInTheDocument();
    expect(screen.queryByText('任务详情 task-1')).not.toBeInTheDocument();
    rerender(<DetailWorkspace {...props} groups={[{...group,tasks}]}/>);
    expect(screen.queryByText('执行正文1')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'展开提示词 · 2份'}));
    rerender(<DetailWorkspace {...props} groups={[{...group,tasks:tasks.map(task=>({...task,status:'completed'}))}]}/>);
    expect(screen.getByText('执行正文1')).toBeInTheDocument();
  });
  it('主图详情分组，完成顺序不会改变图片位置，详情入口绑定各自任务',()=>{
    const detail={...group,plan_id:'plan-2',kind:'detail_page' as const,tasks:[]};
    render(<DetailWorkspace projectId="project-1" onRefresh={vi.fn()} groups={[detail,{...group,tasks}]}/>);
    const main=screen.getByRole('region',{name:'主图工作区'});
    const detailRegion=screen.getByRole('region',{name:'详情图工作区'});
    expect(main.compareDocumentPosition(detailRegion)&Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole('separator',{name:'主图与详情图分区'})).toBeInTheDocument();
    expect(within(main).getAllByRole('heading',{level:3}).map(node=>node.textContent)).toEqual(['1. 主图1','2. 主图2']);
    expect(within(main).getByRole('button',{name:'任务详情 task-1'})).toBeInTheDocument();
    expect(within(detailRegion).queryByText('任务详情 task-1')).not.toBeInTheDocument();
  });
  it('重试响应不确定时继续使用相同幂等键，其他图片保留',async()=>{
    vi.mocked(chatImageService.replay).mockRejectedValue(new Error('连接中断'));
    const onRefresh=vi.fn();
    render(<DetailWorkspace projectId="project-1" onRefresh={onRefresh} groups={[{...group,tasks:tasks.map(task=>({...task,status:task.id==='task-1'?'failed':'completed'}))}]}/>);
    fireEvent.click(screen.getByRole('button',{name:'重新生成'}));
    await waitFor(()=>expect(onRefresh).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button',{name:'重新生成'}));
    await waitFor(()=>expect(onRefresh).toHaveBeenCalledTimes(2));
    expect(vi.mocked(chatImageService.replay).mock.calls[0]).toEqual(vi.mocked(chatImageService.replay).mock.calls[1]);
    expect(screen.getByText('图片 task-2')).toBeInTheDocument();
  });
  it('没有完成原图时不能批量下载',()=>{
    render(<DetailWorkspace projectId="project-1" onRefresh={vi.fn()} groups={[{...group,tasks}]}/>);
    expect(screen.getByRole('button',{name:'批量下载（0张）'})).toBeDisabled();
  });
  it('未确定的调用不提供重试，已关闭的超时允许从失败阶段继续',()=>{
    const props={projectId:'project-1',onRefresh:vi.fn()};
    const failed={...group,status:'failed',stage:2,items:[],error:{code:'MODEL_TIMEOUT',message:'调用超时'}};
    const {rerender}=render(<DetailWorkspace {...props} groups={[{...failed,can_resume:false}]}/>);
    expect(screen.queryByRole('button',{name:'从失败阶段继续'})).not.toBeInTheDocument();
    rerender(<DetailWorkspace {...props} groups={[{...failed,can_resume:true,retry_may_have_provider_cost:true}]}/>);
    expect(screen.getByRole('button',{name:'从失败阶段继续'})).toBeInTheDocument();
    expect(screen.getByText(/旧调用的供应商费用仍待确认/)).toBeInTheDocument();
  });
  it('恢复响应丢失后复用请求编号，详情提示词仍可浏览',async()=>{
    vi.mocked(resumeDetailPlan).mockRejectedValue(new Error('连接中断'));
    render(<DetailWorkspace projectId="project-1" onRefresh={vi.fn()} groups={[
      {...group,kind:'detail_page',acceptance_error:{code:'DETAIL_GENERATION_FAILED'}},
    ]}/>);
    expect(screen.getByText('执行正文1')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'重试提交生图'}));
    await waitFor(()=>expect(screen.getByRole('button',{name:'重试提交生图'})).toBeEnabled());
    fireEvent.click(screen.getByRole('button',{name:'重试提交生图'}));
    await waitFor(()=>expect(resumeDetailPlan).toHaveBeenCalledTimes(2));
    expect(vi.mocked(resumeDetailPlan).mock.calls[0]).toEqual(vi.mocked(resumeDetailPlan).mock.calls[1]);
  });
  it('刷新确认上次恢复已受理后，再次失败可使用新的请求编号',async()=>{
    vi.mocked(resumeDetailPlan).mockRejectedValue(new Error('响应丢失'));
    const props={projectId:'project-1',onRefresh:vi.fn()};
    const failed={...group,status:'failed',stage:2,items:[],can_resume:true};
    const {rerender}=render(<DetailWorkspace {...props} groups={[failed]}/>);
    fireEvent.click(screen.getByRole('button',{name:'从失败阶段继续'}));
    await waitFor(()=>expect(props.onRefresh).toHaveBeenCalledTimes(1));
    const previous=vi.mocked(resumeDetailPlan).mock.calls[0][2];
    rerender(<DetailWorkspace {...props} groups={[{...failed,resume_request_id:previous}]}/>);
    fireEvent.click(screen.getByRole('button',{name:'从失败阶段继续'}));
    await waitFor(()=>expect(resumeDetailPlan).toHaveBeenCalledTimes(2));
    expect(vi.mocked(resumeDetailPlan).mock.calls[1][2]).not.toEqual(previous);
  });
  it('部分完成可打包，下载中防止重复点击，失败后保留结果并可重试',async()=>{
    let rejectDownload:(error:Error)=>void=()=>{};
    vi.mocked(downloadWorkspaceZip).mockImplementationOnce(()=>new Promise((_,reject)=>{rejectDownload=reject;}));
    render(<DetailWorkspace projectId="project-1" onRefresh={vi.fn()} groups={[{...group,tasks:tasks.map(task=>task.id==='task-1'?
      {...task,status:'completed',result_data:{workspace_path:'images/product.png'}}:task)}]}/>);
    fireEvent.click(screen.getByRole('button',{name:'批量下载（1张）'}));
    expect(screen.getByRole('button',{name:'打包中…'})).toBeDisabled();
    fireEvent.click(screen.getByRole('button',{name:'打包中…'}));
    expect(downloadWorkspaceZip).toHaveBeenCalledTimes(1);
    expect(downloadWorkspaceZip).toHaveBeenCalledWith(['images/product.png'],{
      archivePaths:['主图/01-主图1.png'],archiveName:expect.stringMatching(/^主图详情-.*\.zip$/),
    });
    rejectDownload(new Error('网络中断'));
    await waitFor(()=>expect(screen.getByRole('button',{name:'批量下载（1张）'})).toBeEnabled());
    expect(screen.getByText('图片 task-1')).toBeInTheDocument();
    vi.mocked(downloadWorkspaceZip).mockResolvedValueOnce();
    fireEvent.click(screen.getByRole('button',{name:'批量下载（1张）'}));
    await waitFor(()=>expect(downloadWorkspaceZip).toHaveBeenCalledTimes(2));
  });
});
