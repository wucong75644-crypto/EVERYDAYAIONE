import { useState } from 'react';
import { ImageIcon, LoaderCircle, Plus, Trash2 } from 'lucide-react';
import type { DetailTaskSummary } from '../../types/detailPage';
import { formatDateGroup } from '../chat/layout/conversationUtils';
import { Button } from '../ui/Button';
import BaseContextMenu from '../chat/menus/BaseContextMenu';
import DeleteConfirmModal from '../chat/modals/DeleteConfirmModal';

const labels: Record<DetailTaskSummary['display_status'], string> = {
  draft:'草稿',queued:'排队中',selling_points:'卖点分析',visual_direction:'视觉定位',prompts:'提示词生成',
  generating:'生图中',recovering:'自动恢复中',completed:'已完成',needs_attention:'需要处理',
};
interface Props {
  tasks:DetailTaskSummary[]; selectedId:string|null; disabled:boolean; loading:boolean; hasMore:boolean;
  error:string|null; onCreate:()=>void; onSelect:(id:string)=>void; onMore:()=>void; onRefresh:()=>void;
  onDelete?:(id:string)=>Promise<void>;
}
export function DetailTaskSidebar({tasks,selectedId,disabled,loading,hasMore,error,onCreate,onSelect,onMore,onRefresh,onDelete}:Props){
  const [menu,setMenu]=useState<{x:number;y:number;task:DetailTaskSummary}|null>(null);
  const [deleting,setDeleting]=useState<DetailTaskSummary|null>(null);
  const [deletePending,setDeletePending]=useState(false);
  return <aside aria-label="主图详情任务列表" className="flex h-full min-h-0 flex-col gap-3"
    onBlur={event=>{if(!event.currentTarget.contains(event.relatedTarget))setMenu(null);}}>
    <Button className="w-full shrink-0" disabled={disabled} icon={<Plus className="h-4 w-4"/>} onClick={onCreate}>新建任务</Button>
    {disabled&&<p className="text-xs text-[var(--s-text-tertiary)]">上传或保存完成后可新建、切换任务</p>}
    {error&&<div role="alert" className="text-xs text-[var(--s-error)]">{error}<button type="button" className="ml-2 underline" onClick={onRefresh}>重试</button></div>}
    <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain" tabIndex={0} aria-label="任务记录">
      {!tasks.length&&!loading&&<p className="py-6 text-center text-sm text-[var(--s-text-tertiary)]">新建任务后，记录会保存在这里</p>}
      {tasks.map((task,index)=>{
        const date=formatDateGroup(task.created_at);const showDate=index===0||date!==formatDateGroup(tasks[index-1].created_at);
        const active=['queued','selling_points','visual_direction','prompts','generating','recovering'].includes(task.display_status);
        return <div key={task.id}>
          {showDate&&<p className="px-2 pb-2 pt-4 text-xs text-[var(--s-text-tertiary)]">{date}</p>}
          <button type="button" disabled={disabled} onClick={()=>onSelect(task.id)} aria-current={selectedId===task.id?'true':undefined}
            onContextMenu={event=>{if(disabled||!onDelete)return;event.preventDefault();setMenu({x:event.clientX,y:event.clientY,task});}}
            onKeyDown={event=>{if(onDelete&&!disabled&&(event.key==='ContextMenu'||(event.shiftKey&&event.key==='F10'))){
              event.preventDefault();const rect=event.currentTarget.getBoundingClientRect();setMenu({x:rect.left,y:rect.bottom,task});}}}
            className={`mb-1 flex w-full items-center gap-2 rounded-xl p-2 text-left transition-colors disabled:cursor-wait ${selectedId===task.id?'bg-blue-50 ring-1 ring-inset ring-blue-100 dark:bg-blue-950/40':'hover:bg-[var(--s-surface-sunken)]'}`}>
            <span className="flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-[var(--s-surface-sunken)]">
              {task.thumbnail_url?<img src={task.thumbnail_url} alt="" loading="lazy" className="h-full w-full object-cover"/>:<ImageIcon className="h-5 w-5 text-[var(--s-text-tertiary)]"/>}
            </span>
            <span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium" title={task.title}>{task.title}</span>
              <span className="mt-1 flex items-center gap-1 text-xs text-[var(--s-text-secondary)]">
                {active&&<LoaderCircle className="h-3 w-3 shrink-0 animate-spin motion-reduce:animate-none" aria-hidden="true"/>}
                <span>{labels[task.display_status]}</span><span className="ml-auto tabular-nums">{task.completed_count}/{task.expected_count}</span>
              </span>
            </span>
          </button>
        </div>;
      })}
      {hasMore&&<Button variant="ghost" size="sm" className="mt-3 w-full" disabled={loading} onClick={onMore}>{loading?'加载中…':'加载更多任务'}</Button>}
    </div>
    <p className="shrink-0 text-xs leading-5 text-[var(--s-text-tertiary)]">已提交的任务在后台继续执行，可离开页面后回来查看和下载。</p>
    {menu&&!disabled&&tasks.some(task=>task.id===menu.task.id)&&<BaseContextMenu x={menu.x} y={menu.y} onClose={()=>setMenu(null)} items={[{label:'删除',icon:Trash2,tone:'danger',
      onClick:()=>{setDeleting(menu.task);setMenu(null);}}]}/>}
    <DeleteConfirmModal isOpen={!!deleting} title="确定删除任务？" description={`删除“${deleting?.title??''}”后，将从任务列表移除；工作区图片保留。正在运行的任务需完成后再删除。`}
      isDeleting={deletePending} onCancel={()=>setDeleting(null)} onConfirm={()=>{
        if(!deleting||!onDelete||deletePending)return;
        setDeletePending(true);void onDelete(deleting.id).finally(()=>{setDeletePending(false);setDeleting(null);});
      }}/>
  </aside>;
}
