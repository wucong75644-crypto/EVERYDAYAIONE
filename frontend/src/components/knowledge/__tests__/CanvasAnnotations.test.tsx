vi.mock('../DerivedDirectory',()=>({DerivedDirectory:()=>null,DerivedTags:()=>null}));
import { fireEvent,render,screen,waitFor,cleanup } from '@testing-library/react';
import { afterEach,describe,it,expect,vi } from 'vitest';
import { CanvasAnnotationDialog,CanvasGroupOverlays } from '../CanvasAnnotations';
import type { KnowledgeNode } from '../../../services/knowledge';
const node:KnowledgeNode={id:'a',node_id:'la',container_id:'s',parent_id:'r',title:'A',kind:'topic',revision:1,layout_revision:1,x:50,y:60};
afterEach(cleanup);
describe('canvas annotation editor',()=>{
 it('saves notes and retains editor on failure',async()=>{
  const save=vi.fn().mockResolvedValueOnce(false).mockResolvedValue(true),close=vi.fn();render(<CanvasAnnotationDialog node={node} mode="note" writable pending={false} onSave={save} onClose={close}/>);
  fireEvent.change(screen.getByLabelText('备注内容'),{target:{value:'售后说明'}});fireEvent.click(screen.getByText('保存备注'));
  await waitFor(()=>expect(save).toHaveBeenCalledWith({note:'售后说明'}));expect(close).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('保存备注'));await waitFor(()=>expect(close).toHaveBeenCalledTimes(1));
 });
 it('deduplicates labels and preserves the other annotation fields',async()=>{
  const save=vi.fn().mockResolvedValue(true);render(<CanvasAnnotationDialog node={{...node,canvas_data:{note:'留住'}}} mode="labels" writable pending={false} onSave={save} onClose={()=>{}}/>);
  fireEvent.change(screen.getByLabelText('标签内容'),{target:{value:'售后，售后,退款'}});fireEvent.click(screen.getByText('保存标签'));
  await waitFor(()=>expect(save).toHaveBeenCalledWith({note:'留住',labels:['售后','退款']}));
 });
 it('validates dates before saving a task and supports removal',async()=>{
  const save=vi.fn().mockResolvedValue(true);render(<CanvasAnnotationDialog node={{...node,canvas_data:{task:{progress:25,priority:1,start:'2026-10-24',due:'2026-10-25',assignee:'张'}}}} mode="task" writable pending={false} onSave={save} onClose={()=>{}}/>);
  fireEvent.change(screen.getByLabelText('任务截止日期'),{target:{value:'2026-10-23'}});fireEvent.click(screen.getByText('保存任务'));expect(screen.getByRole('alert')).toHaveTextContent('截止日期');expect(save).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('移除任务'));await waitFor(()=>expect(save).toHaveBeenCalledWith({}));
 });
 it('makes readonly fields and save unavailable',()=>{
  render(<CanvasAnnotationDialog node={node} mode="note" writable={false} pending={false} onSave={vi.fn()} onClose={()=>{}}/>);expect(screen.getByLabelText('备注内容')).toBeDisabled();expect(screen.queryByText('保存备注')).toBeNull();
 });
 it('renders an actual summary bracket and a clickable boundary title',()=>{
  const boundary=vi.fn();render(<CanvasGroupOverlays nodes={[{...node,canvas_data:{boundary:{members:['a'],title:'售后组'}}},{...node,id:'summary',x:600,canvas_data:{summary:{members:['a'],title:'概要'}}}]} sizes={new Map()} onSummary={()=>{}} onBoundary={boundary}/>);
  expect(screen.getByLabelText('概要连接 A').querySelector('path')).toHaveAttribute('d');fireEvent.click(screen.getByText('售后组'));expect(boundary).toHaveBeenCalled();
 });
});

it('keeps unknown save results locked and exposes receipt verification inside the dialog',()=>{
 const check=vi.fn();render(<CanvasAnnotationDialog node={node} mode="note" writable pending={false} unknown saveError="结果未知" onCheck={check} onSave={vi.fn()} onClose={()=>{}}/>);
 expect(screen.getByLabelText('备注内容')).toBeDisabled();expect(screen.getByText('保存备注')).toBeDisabled();fireEvent.click(screen.getByText('核对保存结果'));expect(check).toHaveBeenCalledOnce();
});

it('separates overlapping boundary titles and displays only the selected frame handles',()=>{const select=vi.fn(),a={...node,canvas_data:{boundaries:[{id:'one',title:'外框一',members:['a']},{id:'two',title:'外框二',members:['a']}]}};const {rerender}=render(<CanvasGroupOverlays nodes={[a]} sizes={new Map()} selected={null} onSelect={select} onSummary={()=>{}} onBoundary={()=>{}} onRange={vi.fn()}/>);const titles=[screen.getByRole('button',{name:'外框一'}),screen.getByRole('button',{name:'外框二'})];expect(titles[0].style.top).not.toBe(titles[1].style.top);expect(screen.queryByLabelText('调整外框结束范围 A')).toBeNull();fireEvent.click(titles[1]);expect(select).toHaveBeenCalledWith({node:'a',kind:'boundary',key:'two'});rerender(<CanvasGroupOverlays nodes={[a]} sizes={new Map()} selected={{node:'a',kind:'boundary',key:'two'}} onSelect={select} onSummary={()=>{}} onBoundary={()=>{}} onRange={vi.fn()}/>);expect(screen.getAllByLabelText('调整外框结束范围 A')).toHaveLength(1);});
