/**
 * 积分 Tab — 余额展示 + 充值/扣减表单 + 二次确认 + 流水
 */

import { useState, useEffect, useCallback } from 'react';
import toast from 'react-hot-toast';
import { Button } from '../../ui/Button';
import { Input } from '../../ui/Input';
import { Badge } from '../../ui/Badge';
import Modal from '../../common/Modal';
import {
  rechargeUserCredits,
  getUserCreditsHistory,
  type CreditsHistoryItem,
} from '../../../services/adminUser';
import { formatRelativeCN } from '../../../utils/formatRelativeCN';

interface CreditsTabProps {
  userId: string;
  balance: number;
  status?: 'active' | 'disabled';
  onChanged: () => void;
}

const CHANGE_TYPE_LABEL: Record<string, string> = {
  register_gift: '注册赠送',
  admin_adjust: '管理员调整',
  conversation_cost: '对话消耗',
  image_generation_cost: '图片生成',
  video_generation_cost: '视频生成',
  daily_checkin: '每日签到',
  purchase: '充值',
  refund: '退款',
  partial_refund: '差额退回',
  merge: '账号合并',
};

export default function CreditsTab({ userId, balance, status, onChanged }: CreditsTabProps) {
  // 充值表单
  const [direction, setDirection] = useState<'add' | 'sub'>('add');
  const [amount, setAmount] = useState('');
  const [reason, setReason] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // 二次确认
  const [confirmOpen, setConfirmOpen] = useState(false);

  // 流水
  const [history, setHistory] = useState<CreditsHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const data = await getUserCreditsHistory(userId, { page: 1, page_size: 20 });
      setHistory(data.items);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || '加载流水失败');
    } finally {
      setHistoryLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  const amountNumber = parseInt(amount, 10);
  const isValid = !isNaN(amountNumber) && amountNumber > 0 && amountNumber <= 1_000_000;
  const delta = direction === 'add' ? amountNumber : -amountNumber;
  const newBalance = balance + delta;
  const willOverflow = direction === 'sub' && newBalance < 0;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!isValid) {
      toast.error('请输入 1 ~ 1,000,000 之间的整数');
      return;
    }
    if (willOverflow) {
      toast.error(`余额不足，当前余额 ${balance}`);
      return;
    }
    setConfirmOpen(true);
  };

  const handleConfirm = async () => {
    setSubmitting(true);
    try {
      const result = await rechargeUserCredits(userId, {
        delta,
        reason: reason.trim() || undefined,
      });
      toast.success(`调整成功，新余额 ${result.new_balance}`);
      setAmount('');
      setReason('');
      setConfirmOpen(false);
      onChanged();
      loadHistory();
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || '调整失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* 余额展示 */}
      <div className="flex items-baseline gap-4">
        <div>
          <div className="text-xs text-[var(--s-text-tertiary)] mb-1">当前余额</div>
          <div className="text-3xl font-bold font-mono">{balance}</div>
        </div>
        {status === 'disabled' && (
          <Badge variant="error" size="sm">用户已禁用</Badge>
        )}
      </div>

      {/* 充值表单 */}
      <form
        onSubmit={handleSubmit}
        className="border border-[var(--s-border-default)] rounded-lg p-4 space-y-3"
      >
        <div className="text-sm font-medium">调整积分</div>

        <div className="flex gap-2">
          <label className="flex items-center gap-2 flex-1 cursor-pointer">
            <input
              type="radio"
              checked={direction === 'add'}
              onChange={() => setDirection('add')}
            />
            <span>增加</span>
          </label>
          <label className="flex items-center gap-2 flex-1 cursor-pointer">
            <input
              type="radio"
              checked={direction === 'sub'}
              onChange={() => setDirection('sub')}
            />
            <span>扣减</span>
          </label>
        </div>

        <Input
          type="number"
          label="数量"
          placeholder="正整数，1 ~ 1,000,000"
          min={1}
          max={1_000_000}
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          error={willOverflow ? `扣减后余额将为 ${newBalance}（负数）` : undefined}
        />

        <Input
          label="备注（可选）"
          placeholder="如：活动补偿 / 误扣回滚"
          maxLength={200}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />

        <div className="flex justify-end">
          <Button type="submit" variant="accent" disabled={!isValid || willOverflow}>
            {direction === 'add' ? '充值' : '扣减'}
          </Button>
        </div>
      </form>

      {/* 流水 */}
      <div>
        <div className="text-sm font-medium mb-2">最近流水（20 条）</div>
        <div className="border border-[var(--s-border-default)] rounded-lg overflow-hidden">
          {historyLoading ? (
            <div className="text-center py-6 text-[var(--s-text-tertiary)] text-sm">加载中...</div>
          ) : history.length === 0 ? (
            <div className="text-center py-6 text-[var(--s-text-tertiary)] text-sm">暂无流水</div>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-[var(--s-bg-secondary)] text-left text-xs">
                <tr>
                  <th className="px-3 py-2 text-[var(--s-text-secondary)]">变化</th>
                  <th className="px-3 py-2 text-[var(--s-text-secondary)]">类型</th>
                  <th className="px-3 py-2 text-[var(--s-text-secondary)]">备注</th>
                  <th className="px-3 py-2 text-[var(--s-text-secondary)]">操作员</th>
                  <th className="px-3 py-2 text-right text-[var(--s-text-secondary)]">余额</th>
                  <th className="px-3 py-2 text-right text-[var(--s-text-secondary)]">时间</th>
                </tr>
              </thead>
              <tbody>
                {history.map((h) => (
                  <tr key={h.id} className="border-t border-[var(--s-border-default)]">
                    <td className={`px-3 py-2 font-mono font-medium ${
                      h.change_amount > 0 ? 'text-green-600' : 'text-red-600'
                    }`}>
                      {h.change_amount > 0 ? '+' : ''}{h.change_amount}
                    </td>
                    <td className="px-3 py-2 text-[var(--s-text-secondary)]">
                      {CHANGE_TYPE_LABEL[h.change_type] || h.change_type}
                    </td>
                    <td className="px-3 py-2 text-[var(--s-text-secondary)] max-w-[200px] truncate" title={h.description || ''}>
                      {h.description || '—'}
                    </td>
                    <td className="px-3 py-2 text-[var(--s-text-secondary)]">
                      {h.operator_name || (h.operator_id ? h.operator_id.slice(0, 6) : '—')}
                    </td>
                    <td className="px-3 py-2 text-right font-mono">{h.balance_after}</td>
                    <td className="px-3 py-2 text-right text-[var(--s-text-tertiary)] text-xs whitespace-nowrap">
                      {formatRelativeCN(h.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* 二次确认 Modal */}
      <Modal
        isOpen={confirmOpen}
        onClose={() => !submitting && setConfirmOpen(false)}
        title="确认调整"
        maxWidth="max-w-md"
      >
        <div className="space-y-4">
          <div className="text-sm text-[var(--s-text-secondary)]">
            即将对该用户执行以下操作：
          </div>
          <div className="bg-[var(--s-bg-secondary)] rounded-lg p-3 space-y-1.5 text-sm">
            <div className="flex justify-between">
              <span className="text-[var(--s-text-tertiary)]">操作</span>
              <span className="font-medium">
                {direction === 'add' ? '充值' : '扣减'} {amountNumber} 积分
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--s-text-tertiary)]">当前余额</span>
              <span className="font-mono">{balance}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--s-text-tertiary)]">调整后</span>
              <span className="font-mono font-medium">{newBalance}</span>
            </div>
            {reason && (
              <div className="flex justify-between">
                <span className="text-[var(--s-text-tertiary)]">备注</span>
                <span className="text-right max-w-[60%] truncate" title={reason}>{reason}</span>
              </div>
            )}
          </div>
          {status === 'disabled' && (
            <div className="text-sm text-amber-600 bg-amber-50 dark:bg-amber-950/30 p-2.5 rounded">
              ⚠️ 该用户已被禁用，确认要调整其积分吗？
            </div>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" onClick={() => setConfirmOpen(false)} disabled={submitting}>
              取消
            </Button>
            <Button variant="accent" onClick={handleConfirm} loading={submitting}>
              确认
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
