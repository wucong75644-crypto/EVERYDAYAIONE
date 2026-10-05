import { useEffect, useState } from 'react';
import { toast } from 'react-hot-toast';
import Modal from '../../common/Modal';
import { downloadWorkspaceZip } from '../../../services/workspace';
import { chatImageService, type ChatImageDetails, type ChatImageEstimate } from '../../../services/chatImage';

const phaseLabels: Record<string, string> = {
  queued: '排队中', submitting: '正在提交', accepted: '供应商已受理', uncertain: '正在核实供应商受理',
  settling: '正在保存与结算', published: '已完成结算',
};

export default function ChatImageControls({ taskId }: { taskId: string }) {
  const [open, setOpen] = useState(false);
  const [details, setDetails] = useState<ChatImageDetails>();
  const [previous, setPrevious] = useState<ChatImageDetails>();
  const [error, setError] = useState('');
  const [estimate, setEstimate] = useState<ChatImageEstimate>();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let count = 0;
    async function refresh() {
      try {
        const result = await chatImageService.details(taskId);
        if (!active) return;
        setDetails(result); setError('');
        if (result.submission_state !== 'published' && ++count < 60) timer = setTimeout(refresh, 5000);
      } catch { if (active) setError('暂时无法读取实际输入，请重新打开详情重试'); }
    }
    void refresh();
    return () => { active = false; clearTimeout(timer); };
  }, [open, taskId]);

  const stop = async () => {
    setBusy(true);
    try {
      const result = await chatImageService.stop(taskId);
      if (result.outcome === 'stopped') {
        toast.success('排队任务已停止，将完成状态收尾');
        setDetails(current => current && { ...current, can_stop: false, submission_state: 'settling' });
      } else toast(result.message || '任务已领取或提交，无法撤回，将继续核实与结算');
    } catch { toast.error('停止状态未确认，请刷新详情核实'); }
    finally { setBusy(false); }
  };
  const feedback = async (rating: 'helpful' | 'not_helpful') => {
    setBusy(true);
    try {
      await chatImageService.feedback(taskId, rating);
      setDetails(current => current && { ...current, feedback: { rating } });
      toast.success('已记录该版本的反馈');
    } catch { toast.error('反馈保存失败'); }
    finally { setBusy(false); }
  };
  const compare = async () => {
    if (!details?.input.origin.retry_of_task_id) return;
    setBusy(true);
    try { setPrevious(await chatImageService.details(details.input.origin.retry_of_task_id)); }
    catch { toast.error('旧版本暂时无法读取'); }
    finally { setBusy(false); }
  };
  const downloadComparison = async () => {
    const paths = [previous?.result?.[0]?.workspace_path, details?.result?.[0]?.workspace_path]
      .filter((path): path is string => !!path);
    if (paths.length !== 2) return;
    setBusy(true);
    try { await downloadWorkspaceZip(paths); }
    catch { toast.error('对比图下载失败，请检查工作区文件是否仍可访问'); }
    finally { setBusy(false); }
  };
  const previewCost = async () => {
    if (!details) return;
    setBusy(true);
    try { setEstimate(await chatImageService.estimate(details.input)); }
    catch { toast.error('服务器成本预览暂时不可用'); }
    finally { setBusy(false); }
  };
  const copyRecipe = async () => {
    if (!details) return;
    const input = details.input;
    const recipe = { mode: input.mode, prompt: input.prompt,
      aspect_ratio: input.aspect_ratio, resolution: input.resolution, output_format: input.output_format,
      ...(input.background ? { background: input.background } : {}),
      references: input.references.map(({ role, asset_id, message_id, content_index, source_message_id, source_content_index, resource_ref, file_id }) =>
        ({ role, ...(asset_id ? { asset_id } : message_id ? { message_id, content_index }
          : source_message_id ? { message_id: source_message_id, content_index: source_content_index }
          : file_id ? { file_id } : { resource_ref }) })) };
    try { await navigator.clipboard.writeText(JSON.stringify(recipe, null, 2)); toast.success('已复制配方；原图引用仍需当前用户权限'); }
    catch { toast.error('复制失败，可直接选择下方提示词'); }
  };

  return <div className="mt-2 text-xs text-text-secondary">
    <button type="button" onClick={() => setOpen(value => !value)} className="hover:text-text-primary underline">图片任务详情</button>
    <Modal isOpen={open} onClose={() => setOpen(false)} title="图片任务详情" maxWidth="max-w-[760px]">
    <div className="max-h-[min(calc(90dvh-120px),680px)] overflow-y-auto space-y-4 text-sm text-text-secondary break-words">
      {error && <p role="alert">{error}</p>}
      {!details && !error && <p>正在读取实际输入…</p>}
      {details && <>
        {details.result?.[0]?.url && <img src={details.result[0].url} alt="当前任务图片" className="max-h-40 max-w-full rounded-lg object-contain" />}
        <p>{phaseLabels[details.submission_state] || details.submission_state} · 预估 {details.input.estimated_credits} 积分 · 已结算 {details.credits_used} 积分</p>
        <p>{details.input.mode === 'image_to_image' ? '图生图' : '文生图'} · {details.input.model} · {details.input.aspect_ratio} · {details.input.resolution || '模型默认分辨率'} · {details.input.output_format}</p>
        {details.input.size_requirement?.mode === 'inherit_reference' && details.input.size_requirement.original_width && details.input.size_requirement.original_height && <p>画布比例沿用所选原图：{details.input.size_requirement.original_width} × {details.input.size_requirement.original_height}</p>}
        <p className="font-medium">服务器实际执行提示词</p>
        <pre className="whitespace-pre-wrap break-words select-text font-sans">{details.input.prompt}</pre>
        {details.input.references.length > 0 && <ol className="list-decimal pl-5 space-y-1">
          {details.input.references.map((reference, index) => <li key={index} className="break-all">
            {reference.role} · {reference.workspace_path} · 原图 {reference.size} 字节
            {(reference.message_id || reference.source_message_id) && <p className="text-text-tertiary">
              来源消息：{reference.message_id || reference.source_message_id} · 原始图片块 {reference.content_index ?? reference.source_content_index}
              {reference.source && ` · ${({ uploaded: '用户上传', generated: '生成结果', quoted: '用户引用', file_search: '文件搜索' })[reference.source]}`}
            </p>}
            {reference.quoted_message_id && <p>引用原消息：{reference.quoted_message_id} · 图片块 {reference.quoted_content_index}</p>}
            {details.reference_previews?.[index]?.url && <img src={details.reference_previews[index].url!} alt={`参考图 ${index + 1}：${reference.role}`} className="mt-1 max-h-24 rounded object-contain" />}
          </li>)}
        </ol>}
        <details className="rounded-lg border border-border p-3 space-y-2">
          <summary className="cursor-pointer">更多信息</summary>
          <p className="break-all">任务 ID：{details.task_id}</p>
          <p>本轮图片累计预算 {details.input.budget.max_credits} 积分；跨对话共享最多15个活跃任务，满额排队。</p>
        {details.input.source_prompt && <p className="break-all">提示词来源：{JSON.stringify(details.input.source_prompt)}</p>}
        {(details.input.plan_item_id || details.input.variant_id) && <p>计划项：{details.input.plan_item_id || '—'} · 变体：{details.input.variant_id || '—'}</p>}
        </details>
        <p>{details.cancel_explanation}</p>
        {details.input.background && <p>背景要求：{details.input.background === 'transparent' ? '真实透明背景' : '不透明背景'}</p>}
        {details.platform_cost && <p>{details.platform_cost.reason === 'submission_uncertain_expired' ? '该任务受理未确认' : '供应商结果未满足图片合同'}，已退还 {details.platform_cost.refunded_user_credits} 积分；供应商费用由平台承担并记录估算。</p>}
        <div className="flex flex-wrap gap-3">
          {details.can_stop && <button type="button" disabled={busy} onClick={() => void stop()}>停止排队</button>}
          <button type="button" disabled={busy} onClick={() => void previewCost()}>预览再生成成本</button>
          <button type="button" onClick={() => void copyRecipe()}>复制配方</button>
          {previous?.result?.[0]?.workspace_path && details.result?.[0]?.workspace_path && <button type="button" disabled={busy} onClick={() => void downloadComparison()}>下载对比图 ZIP</button>}
          {details.input.origin.retry_of_task_id && <button type="button" disabled={busy} onClick={() => void compare()}>比较旧版本</button>}
          {details.submission_state === 'published' && <>
            <button type="button" disabled={busy} aria-pressed={details.feedback?.rating === 'helpful'} onClick={() => void feedback('helpful')}>满意</button>
            <button type="button" disabled={busy} aria-pressed={details.feedback?.rating === 'not_helpful'} onClick={() => void feedback('not_helpful')}>需要改进</button>
          </>}
        </div>
        {estimate && <p>服务器当前估算：每张 {estimate.per_image_credits} 积分。{estimate.acceptance_enabled ? '' : '暂未开放新任务。'}{estimate.within_budget ? '' : '超出本轮预算。'}新版本仍会重新校验原图权限和价格。</p>}
        {details.result?.[0]?.quality_checks?.actual && <p>实际画布：{details.result[0].quality_checks.actual.width} × {details.result[0].quality_checks.actual.height}（{details.result[0].quality_checks.actual.aspect_ratio}）{details.result[0].quality_checks.size_matches === false ? '，未满足尺寸要求' : ''}</p>}
        {details.result?.[0]?.has_transparency && <p>已校验保存结果含真实透明像素。</p>}
        {previous && <div className="grid grid-cols-2 gap-2">
          {[previous, details].map((version, index) => <div key={version.task_id}>
            <p>{index === 0 ? '旧版本' : '当前版本'}</p>
            {version.result?.[0]?.url ? <img src={version.result[0].url} alt={index === 0 ? '旧版本图片' : '当前版本图片'} className="rounded-lg w-full" /> : <p>此版本没有可用结果</p>}
          </div>)}
        </div>}
      </>}
    </div>
    </Modal>
  </div>;
}
