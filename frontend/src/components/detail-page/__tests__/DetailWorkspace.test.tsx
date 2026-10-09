import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { DetailGroup } from '../../../types/detailPage';
import { chatImageService } from '../../../services/chatImage';
import { DetailWorkspace } from '../DetailWorkspace';
import { downloadWorkspaceZip } from '../../../services/workspace';

vi.mock('../../chat/media/ChatImageControls', () => ({ default: ({taskId}:{taskId:string}) => <button>任务详情 {taskId}</button> }));
vi.mock('../../chat/message/MessageImageBlocks', () => ({ AiGeneratedImage: ({renderId}:{renderId:string}) => <div>图片 {renderId}</div> }));
vi.mock('../../chat/media/ImagePreviewModal', () => ({ default: () => null }));
vi.mock('../../chat/media/MediaPlaceholder', () => ({ FailedMediaPlaceholder: ({onRetry}:{onRetry:()=>void}) => <button onClick={onRetry}>重新生成</button> }));
vi.mock('../../../services/chatImage', () => ({ chatImageService: {replay:vi.fn()} }));
vi.mock('../../../services/workspace', () => ({ downloadWorkspaceZip: vi.fn() }));

const group:DetailGroup = {
  plan_id:'plan-1',kind:'main_images',status:'ready',stage:3,count:2,tasks:[],
  items:[1,2].map(position=>({item_id:`item-${position}`,position,name:`主图${position}`,purpose:'商品展示',request_text:`执行正文${position}`,aspect_ratio:'1:1'})),
};
const tasks:DetailGroup['tasks'] = [2,1].map(position=>({id:`task-${position}`,item_id:`item-${position}`,status:'pending',submission_state:'queued',created_at:'2026-10-09'}));

describe('右侧提示词和图片工作区',()=>{
  beforeEach(()=>vi.clearAllMocks());
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
