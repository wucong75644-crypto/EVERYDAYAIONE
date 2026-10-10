import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DetailTaskSidebar } from '../DetailTaskSidebar';
import type { DetailTaskSummary } from '../../../types/detailPage';
const task:DetailTaskSummary={id:'a',title:'手账本',created_at:new Date().toISOString(),content_type:'default',status:'generating',display_status:'generating',completed_count:8,expected_count:14,thumbnail_url:null,stage:null,recovery_waiting:false};
function setup(disabled=false){const onCreate=vi.fn(),onSelect=vi.fn();render(<DetailTaskSidebar tasks={[task,{...task,id:'b',title:'存钱本',display_status:'queued'}]} selectedId="a" disabled={disabled} loading={false} hasMore={false} error={null} onCreate={onCreate} onSelect={onSelect} onMore={vi.fn()} onRefresh={vi.fn()}/>);return {onCreate,onSelect};}
describe('右侧任务列表',()=>{
 it('复用右键菜单与删除确认，删除不触发任务切换',async()=>{
  const onDelete=vi.fn().mockResolvedValue(undefined),onSelect=vi.fn();
  render(<DetailTaskSidebar tasks={[task]} selectedId="a" disabled={false} loading={false} hasMore={false} error={null}
    onCreate={vi.fn()} onSelect={onSelect} onMore={vi.fn()} onRefresh={vi.fn()} onDelete={onDelete}/>);
  fireEvent.contextMenu(screen.getByRole('button',{name:/手账本/}),{clientX:150,clientY:200});
  fireEvent.click(screen.getByRole('button',{name:'删除'}));
  expect(onSelect).not.toHaveBeenCalled();expect(onDelete).not.toHaveBeenCalled();
  const dialog=screen.getByRole('dialog');expect(within(dialog).getByText('确定删除任务？')).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole('button',{name:'删除'}));
  await waitFor(()=>expect(onDelete).toHaveBeenCalledExactlyOnceWith('a'));
 });
 it('显示名称、状态、完成数量和选中项，允许新建和切换',()=>{const actions=setup();expect(screen.getAllByText('8/14')).toHaveLength(2);expect(screen.getByText('排队中')).toBeInTheDocument();const selected=screen.getByRole('button',{name:/手账本/});expect(selected).toHaveAttribute('aria-current','true');fireEvent.click(screen.getByRole('button',{name:'新建任务'}));fireEvent.click(screen.getByRole('button',{name:/存钱本/}));expect(actions.onCreate).toHaveBeenCalledOnce();expect(actions.onSelect).toHaveBeenCalledWith('b');});
 it('上传和切换期间禁用新建和任务切换',()=>{const actions=setup(true);fireEvent.click(screen.getByRole('button',{name:'新建任务'}));fireEvent.click(screen.getByRole('button',{name:/存钱本/}));expect(actions.onCreate).not.toHaveBeenCalled();expect(actions.onSelect).not.toHaveBeenCalled();});
});
