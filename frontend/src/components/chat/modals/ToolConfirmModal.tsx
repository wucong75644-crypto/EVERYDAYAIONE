/**
 * 工具写操作确认弹窗
 *
 * 当 AI 要执行写操作（如修改订单、触发同步）时弹出，
 * 用户可以确认执行或拒绝。60s 超时自动关闭。
 *
 * Phase 3 B5 — 写操作确认流
 */

import { useEffect, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import Modal from '../../common/Modal';
import { Button } from '../../ui/Button';

/** 工具名称映射 */
const TOOL_LABELS: Record<string, string> = {
  erp_execute: 'ERP 写操作',
  trigger_erp_sync: 'ERP 数据同步',
};

export interface ToolConfirmRequest {
  toolCallId: string;
  toolName: string;
  arguments: Record<string, unknown>;
  description: string;
  timeout: number;
}

interface ToolConfirmModalProps {
  request: ToolConfirmRequest | null;
  onConfirm: (toolCallId: string) => void;
  onReject: (toolCallId: string) => void;
}

export default function ToolConfirmModal({
  request,
  onConfirm,
  onReject,
}: ToolConfirmModalProps) {
  const [countdown, setCountdown] = useState(60);

  // 倒计时
  useEffect(() => {
    if (!request) return;
    setCountdown(request.timeout || 60);

    const timer = setInterval(() => {
      setCountdown((prev) => {
        if (prev <= 1) {
          clearInterval(timer);
          onReject(request.toolCallId);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    return () => clearInterval(timer);
  }, [request, onReject]);

  if (!request) return null;

  const label = TOOL_LABELS[request.toolName] || request.toolName;

  // 格式化参数摘要
  const argEntries = Object.entries(request.arguments).slice(0, 5);

  return (
    <Modal isOpen showCloseButton={false} maxWidth="max-w-md">
      <div className="flex items-start gap-3">
        <div className="w-10 h-10 bg-warning-light rounded-full flex items-center justify-center flex-shrink-0">
          <AlertTriangle className="w-5 h-5 text-warning" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-lg font-medium text-text-primary">
            写操作确认
          </h3>
          <p className="mt-1 text-sm text-text-secondary">
            {request.description || `AI 要执行: ${label}`}
          </p>
        </div>
      </div>

      {/* 参数摘要 */}
      {argEntries.length > 0 && (
        <div className="mt-3 p-3 rounded-lg bg-surface-secondary text-sm font-mono">
          {argEntries.map(([key, value]) => (
            <div key={key} className="flex gap-2 text-text-secondary">
              <span className="text-text-tertiary shrink-0">{key}:</span>
              <span className="truncate">
                {typeof value === 'string' ? value : JSON.stringify(value)}
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
            onClick={() => onReject(request.toolCallId)}
          >
            拒绝
          </Button>
          <Button
            variant="accent"
            size="md"
            onClick={() => onConfirm(request.toolCallId)}
          >
            确认执行
          </Button>
        </div>
      </div>
    </Modal>
  );
}
