import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { DetailGroup } from '../../../types/detailPage';
import { DetailPlanProgress } from '../DetailPlanProgress';

const group: DetailGroup = {
  plan_id: 'main-plan', kind: 'main_images', status: 'planning', stage: 1, count: 7, items: [], tasks: [],
};

describe('真实任务状态驱动的三阶段进度', () => {
  it('自动恢复只激活未完成阶段，平台延迟恢复保留真实进度',()=>{
    const failed:DetailGroup={...group,status:'failed',stage:2,can_resume:false,
      auto_recovery:{enabled:true,status:'waiting',retry_cost:'platform',items:{},attempts:5,platform_attention:true}};
    render(<DetailPlanProgress group={failed}/>);
    expect(screen.getByRole('listitem',{name:'卖点分析：已完成'})).toBeInTheDocument();
    expect(screen.getByRole('listitem',{name:'视觉定位：正在执行'})).toHaveAttribute('aria-current','step');
    expect(screen.getByRole('status')).toHaveTextContent('视觉定位自动恢复中');
    expect(screen.getByText(/任务和已完成结果已保留/)).toBeInTheDocument();
  });
  it('生图自动恢复只统计失败项；硬性阻断仍显示失败',()=>{
    const accepted:DetailGroup={...group,stage:3,status:'ready',count:2,
      items:[1,2].map(position=>({item_id:`item-${position}`,position,name:'主图',purpose:'',request_text:'',aspect_ratio:'1:1'})),
      tasks:[1,2].map(position=>({id:`task-${position}`,item_id:`item-${position}`,status:position===1?'completed':'failed',submission_state:'published',created_at:'2026-10-10'})),
      auto_recovery:{enabled:true,status:'waiting',retry_cost:'platform',items:{'item-2':{status:'waiting',attempts:2}}}};
    const {rerender}=render(<DetailPlanProgress group={accepted}/>);
    expect(screen.getByRole('status')).toHaveTextContent('已完成 1/2 · 自动补图 1张');
    expect(screen.getAllByRole('listitem',{name:/已完成/})).toHaveLength(3);
    rerender(<DetailPlanProgress group={{...accepted,auto_recovery:{...accepted.auto_recovery!,status:'blocked',items:{'item-2':{status:'blocked',attempts:2}}}}}/>);
    expect(screen.getByRole('status')).toHaveTextContent('已完成 1/2 · 1张失败');
  });
  it('只激活服务端当前步骤，轮询不会替换正在运行的节点', () => {
    const { rerender } = render(<DetailPlanProgress group={group} />);
    const first = screen.getByRole('listitem', { name: '卖点分析：正在执行' });
    expect(first).toHaveAttribute('aria-current', 'step');
    expect(screen.getByRole('listitem', { name: '视觉定位：等待执行' })).not.toHaveAttribute('aria-current');
    expect(screen.getByRole('status')).toHaveTextContent('正在卖点分析');
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    rerender(<DetailPlanProgress group={{ ...group }} />);
    expect(screen.getByRole('listitem', { name: '卖点分析：正在执行' })).toBe(first);
    rerender(<DetailPlanProgress group={{ ...group, stage: 2 }} />);
    expect(screen.getByRole('listitem', { name: '卖点分析：已完成' })).not.toHaveAttribute('aria-current');
    expect(screen.getByRole('listitem', { name: '视觉定位：正在执行' })).toHaveAttribute('aria-current', 'step');
    expect(screen.getByRole('listitem', { name: '逐图提示词：等待执行' })).toBeInTheDocument();
    expect(screen.getByText(/正在规划整组图片的风格/)).toBeInTheDocument();
    rerender(<DetailPlanProgress group={{ ...group, stage: 3 }} />);
    expect(screen.getByRole('listitem', { name: '视觉定位：已完成' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('正在逐图提示词');
  });

  it('失败停止动画，恢复后只激活失败阶段并保留已完成阶段', () => {
    const failed = { ...group, stage: 2, status: 'failed', can_resume: true };
    const { rerender } = render(<DetailPlanProgress group={failed} />);
    expect(screen.getByRole('listitem', { name: '视觉定位：未完成' })).toHaveAttribute('data-state', 'failed');
    expect(screen.queryByRole('listitem', { current: 'step' })).not.toBeInTheDocument();
    expect(screen.getByText('视觉定位未完成。已保留卖点分析结果。')).toBeInTheDocument();
    rerender(<DetailPlanProgress group={{ ...group, stage: 2 }} />);
    expect(screen.getByRole('listitem', { name: '卖点分析：已完成' })).toBeInTheDocument();
    expect(screen.getByRole('listitem', { name: '视觉定位：正在执行' })).toHaveAttribute('aria-current', 'step');
  });

  it('结果待确认保持静止，已关闭且允许恢复的超时显示失败', () => {
    const failed = { ...group, stage: 2, status: 'failed', error: { code: 'MODEL_TIMEOUT', category: 'uncertain' } };
    const { rerender } = render(<DetailPlanProgress group={{ ...failed, can_resume: false }} />);
    expect(screen.getByRole('status')).toHaveTextContent('结果待确认');
    expect(screen.getByRole('listitem', { name: '视觉定位：结果待确认' })).toHaveAttribute('data-state', 'uncertain');
    expect(screen.queryByRole('listitem', { current: 'step' })).not.toBeInTheDocument();
    rerender(<DetailPlanProgress group={{ ...failed, can_resume: true }} />);
    expect(screen.getByRole('status')).toHaveTextContent('未完成');
    expect(screen.getByRole('listitem', { name: '视觉定位：未完成' })).toHaveAttribute('data-state', 'failed');
  });

  it.each(['needs_input', 'insufficient', 'cancelled', 'queued'])('%s 不显示正在执行', status => {
    render(<DetailPlanProgress group={{ ...group, status }} />);
    expect(screen.queryByRole('listitem', { current: 'step' })).not.toBeInTheDocument();
    expect(screen.getByRole('listitem', { name: '视觉定位：等待执行' })).toBeInTheDocument();
  });

  it('受理生图失败仍保留三个完成标记，不把第三阶段显示成失败', () => {
    render(<DetailPlanProgress group={{ ...group, stage: 3, status: 'ready', acceptance_error: { code: 'DETAIL_GENERATION_FAILED' } }} />);
    expect(screen.getAllByRole('listitem', { name: /已完成/ })).toHaveLength(3);
    expect(screen.getByRole('status')).toHaveTextContent('图片任务尚未受理');
    expect(screen.queryByRole('listitem', { current: 'step' })).not.toBeInTheDocument();
  });

  it('主图和详情图分别显示真实进度', () => {
    render(<><section aria-label="主图"><DetailPlanProgress group={{ ...group, stage: 3 }} /></section>
      <section aria-label="详情图"><DetailPlanProgress group={{ ...group, plan_id: 'detail-plan', kind: 'detail_page', stage: 2 }} /></section></>);
    expect(within(screen.getByRole('region', { name: '主图' })).getByRole('status')).toHaveTextContent('正在逐图提示词');
    expect(within(screen.getByRole('region', { name: '详情图' })).getByRole('status')).toHaveTextContent('正在视觉定位');
  });

  it('按每张图片的最新任务统计完成与失败，受理待确认与全部完成有对应说明', () => {
    const accepted: DetailGroup = { ...group, stage: 3, status: 'ready', count: 2,
      items: [1, 2].map(position => ({ item_id: `item-${position}`, position, name: '主图', purpose: '', request_text: '', aspect_ratio: '1:1' })),
      tasks: [
        { id: 'old-1', item_id: 'item-1', status: 'failed', submission_state: 'failed', created_at: '2026-10-10' },
        { id: 'task-1', item_id: 'item-1', status: 'completed', submission_state: 'completed', created_at: '2026-10-10' },
        { id: 'task-2', item_id: 'item-2', status: 'pending', submission_state: 'uncertain', created_at: '2026-10-10' },
      ],
    };
    const { rerender } = render(<DetailPlanProgress group={accepted} />);
    expect(screen.getByRole('status')).toHaveTextContent('已完成 1/2');
    expect(screen.getByText(/部分图片的受理结果尚待确认/)).toBeInTheDocument();
    rerender(<DetailPlanProgress group={{ ...accepted, tasks: accepted.tasks.map(task => task.id === 'task-2' ? { ...task, status: 'failed' } : task) }} />);
    expect(screen.getByRole('status')).toHaveTextContent('已完成 1/2 · 1张失败');
    rerender(<DetailPlanProgress group={{ ...accepted, tasks: accepted.tasks.map(task => ({ ...task, status: 'completed' })) }} />);
    expect(screen.getByRole('status')).toHaveTextContent('已完成 2/2');
    expect(screen.getByText(/本组图片已全部完成/)).toBeInTheDocument();
    expect(screen.getAllByRole('listitem', { name: /已完成/ })).toHaveLength(3);
  });
});
