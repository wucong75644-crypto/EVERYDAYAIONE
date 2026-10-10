import { Check, CircleAlert, Pause, X } from 'lucide-react';
import type { DetailGroup } from '../../types/detailPage';
import { detailTaskFor } from '../../utils/detailPageImages';
import styles from './DetailPlanProgress.module.css';

const stages = [
  { name: '卖点分析', description: '正在分析产品图片与需求，拆分产品细节和卖点…' },
  { name: '视觉定位', description: '正在规划整组图片的风格、背景、配色与光效…' },
  { name: '逐图提示词', description: '正在组织每张图片的构图、文案与生成提示词…' },
];

type StepState = 'pending' | 'running' | 'completed' | 'failed' | 'uncertain' | 'paused' | 'cancelled';
const stateLabels: Record<StepState, string> = {
  pending: '等待执行', running: '正在执行', completed: '已完成', failed: '未完成',
  uncertain: '结果待确认', paused: '需要补充信息', cancelled: '已停止',
};

export function DetailPlanProgress({ group, readOnly=false }: { group: DetailGroup; readOnly?:boolean }) {
  const accepted = group.tasks.length > 0;
  const ready = accepted || group.status === 'ready';
  const current = Math.max(1, Math.min(3, group.stage));
  const stage = stages[current - 1];
  const needsInput = ['needs_input', 'insufficient'].includes(group.status);
  const recovering = group.auto_recovery?.status === 'waiting';
  const uncertain = !readOnly && group.status === 'failed' && !group.can_resume && (
    group.error?.category === 'uncertain' || group.error?.code === 'ECOM_PLAN_EXECUTION_UNCERTAIN'
  );
  const activeState: StepState = recovering ? 'running' : uncertain ? 'uncertain' : needsInput ? 'paused'
    : group.status === 'failed' ? 'failed' : group.status === 'cancelled' ? 'cancelled'
    : group.status === 'planning' && group.execution_state !== 'waiting' ? 'running' : 'pending';
  const latestTasks = group.items.map(item => detailTaskFor(group, item.item_id)).filter(task => !!task);
  const completed = latestTasks.filter(task => task.status === 'completed').length;
  const retryingImages = latestTasks.filter(task => group.auto_recovery?.items[task.item_id]?.status === 'waiting').length;
  const failedImages = latestTasks.filter(task => ['failed', 'cancelled'].includes(task.status)
    && group.auto_recovery?.items[task.item_id]?.status !== 'waiting').length;
  const confirmingImages = latestTasks.some(task => task.submission_state === 'uncertain'
    && !['completed', 'failed', 'cancelled'].includes(task.status));

  let status = activeState === 'running' ? `正在${stage.name}` : stateLabels[activeState];
  let description = activeState === 'running' ? stage.description : `${stage.name}${stateLabels[activeState]}。`;
  let tone: StepState = activeState;
  if (group.status === 'planning' && group.execution_state === 'waiting' && !recovering) {
    status='排队等待策划';
    description='任务已保存，正在等待执行容量；离开页面后仍会继续处理。';
  }
  if (ready) {
    tone = 'completed';
    status = '提示词已完成，准备生图';
    description = '整组提示词已完成，可展开浏览；图片任务受理后将展示生成进度。';
  }
  if (accepted) {
    tone = failedImages ? 'failed' : confirmingImages ? 'uncertain' : 'completed';
    status = `已完成 ${completed}/${group.count}${failedImages ? ` · ${failedImages}张失败` : ''}`;
    description = failedImages ? (readOnly?'已保留本轮结果，可查看已完成图片。':'已完成的图片已保留，可在对应图片下重新生成失败项。')
      : confirmingImages ? '部分图片的受理结果尚待确认，请查看对应图片状态。'
      : completed === group.count ? '本组图片已全部完成，可查看大图或批量下载。'
      : '图片任务已受理，下方按图片显示排队与生成状态。';
  } else if (group.acceptance_error) {
    tone = 'failed';
    status = '图片任务尚未受理';
    description = '提示词已保留，可重试提交生图。';
  } else if (!ready && activeState !== 'running') {
    const preserved = current > 1 ? `已保留${stages.slice(0, current - 1).map(item => item.name).join('、')}结果。` : '';
    description = uncertain ? `${stage.name}的调用结果尚待确认，已停止自动重发。${preserved}`
      : needsInput ? '请根据下方提示补充产品信息后重新开始。'
      : activeState === 'cancelled' ? `任务已停止。${preserved}`
      : activeState === 'failed' ? `${stage.name}未完成。${preserved}` : '任务已保存，正在等待策划任务开始处理；离开页面后仍会继续。';
  }
  if (recovering) {
    tone = 'running';
    status = accepted ? `已完成 ${completed}/${group.count} · 自动补图 ${retryingImages}张`
      : group.acceptance_error ? '正在自动提交图片任务' : `${stage.name}自动恢复中`;
    description = group.auto_recovery?.platform_attention
      ? '服务暂时不可用，任务和已完成结果已保留，系统会延迟继续尝试。'
      : accepted ? '系统正在自动补齐未完成图片，已完成图片保留，无需手动重试。'
      : group.acceptance_error ? '已有提示词已保留，系统会自动重新提交生图。'
      : '已完成阶段保留，系统会从未完成阶段继续；失败重试的额外成本由平台承担。';
  }

  return <div className={styles.progress}>
    <div className={styles.header}>
      <h2 className="font-semibold">{group.kind === 'main_images' ? '主图' : '详情图'} · {group.count}张</h2>
      <span className={styles.status} data-state={tone} role="status" aria-live="polite" aria-atomic="true">
        <span key={status} className={styles.statusText}>{status}</span>
      </span>
    </div>
    <ol className={styles.steps} aria-label="策划阶段">
      {stages.map((item, index) => {
        const state: StepState = ready || index + 1 < current ? 'completed'
          : index + 1 === current ? activeState : 'pending';
        return <li key={item.name} className={styles.step} data-state={state}
          aria-label={`${item.name}：${stateLabels[state]}`} aria-current={state === 'running' ? 'step' : undefined}>
          <div className={styles.stepBody}>
            <span key={state} className={styles.node} aria-hidden="true">
              {state === 'running' && <span className={styles.spinner} />}
              {state === 'completed' && <Check className={styles.icon} />}
              {(state === 'failed' || state === 'uncertain') && <CircleAlert className={styles.icon} />}
              {state === 'paused' && <Pause className={styles.icon} />}
              {state === 'cancelled' && <X className={styles.icon} />}
            </span>
            <span>{item.name}</span>
          </div>
          {index < stages.length - 1 && <span className={styles.connector} aria-hidden="true">
            <span className={styles.connectorFill} />
          </span>}
        </li>;
      })}
    </ol>
    <p key={description} className={styles.description}>{description}</p>
  </div>;
}
