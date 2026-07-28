/**
 * 工具写操作确认弹窗
 *
 * 当 AI 要执行写操作（如修改订单、触发同步）时弹出，
 * 用户可以确认执行或拒绝。60s 超时自动关闭。
 *
 * Phase 3 B5 — 写操作确认流
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import Modal from '../../common/Modal';
import { Button } from '../../ui/Button';

/** 工具名称映射 */
const TOOL_LABELS: Record<string, string> = {
  erp_execute: 'ERP 写操作',
  trigger_erp_sync: 'ERP 数据同步',
  file_delete: '文件删除',
  restore_file: '文件恢复',
  manage_scheduled_task: '计划任务管理',
};

export interface ToolConfirmRequest {
  confirmationId: string;
  toolName: string;
  confirmationSummary: Record<string, string | number | boolean>;
  timeout: number;
}

interface ToolConfirmModalProps {
  request: ToolConfirmRequest | null;
  onConfirm: (confirmationId: string) => void;
  onReject: (confirmationId: string) => void;
}

export default function ToolConfirmModal({
  request,
  onConfirm,
  onReject,
}: ToolConfirmModalProps) {
  const [countdown, setCountdown] = useState(60);
  const respondedRef = useRef(false);

  const respond = useCallback((approved: boolean) => {
    if (!request || respondedRef.current) return;
    respondedRef.current = true;
    if (approved) onConfirm(request.confirmationId);
    else onReject(request.confirmationId);
  }, [onConfirm, onReject, request]);

  // 倒计时
  useEffect(() => {
    if (!request) return;
    respondedRef.current = false;
    const resetTimer = setTimeout(() => setCountdown(request.timeout || 60), 0);

    const timer = setInterval(() => {
      setCountdown((prev) => {
        if (prev <= 1) {
          clearInterval(timer);
          respond(false);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    return () => {
      clearTimeout(resetTimer);
      clearInterval(timer);
    };
  }, [request, respond]);

  if (!request) return null;

  const label = TOOL_LABELS[request.toolName] || request.toolName;

  // 格式化参数摘要
  const summaryEntries = Object.entries(request.confirmationSummary).slice(0, 8);

  return (
    <Modal isOpen onClose={() => respond(false)} showCloseButton={false} maxWidth="max-w-md">
      <div className="flex items-start gap-3">
        <div className="w-10 h-10 bg-warning-light rounded-full flex items-center justify-center flex-shrink-0">
          <AlertTriangle className="w-5 h-5 text-warning" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-lg font-medium text-text-primary">
            写操作确认
          </h3>
          <p className="mt-1 text-sm text-text-secondary">
            AI 要执行：{label}
          </p>
        </div>
      </div>

      {/* 参数摘要 */}
      {summaryEntries.length > 0 && (
        <div className="mt-3 p-3 rounded-lg bg-hover text-sm">
          {summaryEntries.map(([key, value]) => (
            <div key={key} className="flex gap-2 text-text-secondary">
              <span className="text-text-tertiary shrink-0">{key}:</span>
              <span className="truncate">
                {String(value)}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="mt-5 flex items-center justify-between">
        <span className="text-xs text-text-tertiary">
          {countdown}s 后自动取消
        </span>
        <div className="flex gap-3">
          <Button
            variant="secondary"
            size="md"
            onClick={() => respond(false)}
          >
            拒绝
          </Button>
          <Button
            variant="accent"
            size="md"
            onClick={() => respond(true)}
          >
            确认执行
          </Button>
        </div>
      </div>
    </Modal>
  );
}
