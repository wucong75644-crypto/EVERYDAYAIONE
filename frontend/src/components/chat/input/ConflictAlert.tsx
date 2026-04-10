/**
 * 冲突警告组件
 *
 * 显示模型与当前状态的冲突信息，并提供解决方案
 */

import { AlertCircle, Info, X } from 'lucide-react';
import { type ModelConflict } from '../../../utils/modelConflict';
import { type UnifiedModel } from '../../../constants/models';

interface ConflictAlertProps {
  conflict: ModelConflict | null;
  onSwitchModel?: (model: UnifiedModel) => void;
  onRemoveImage?: () => void;
  onClose?: () => void;
}

export default function ConflictAlert({
  conflict,
  onSwitchModel,
  onRemoveImage,
  onClose,
}: ConflictAlertProps) {
  if (!conflict) return null;

  // 根据严重程度确定样式（统一用 token）
  const severityStyles = {
    critical: {
      container: 'bg-error-light border border-error/20',
      icon: 'text-error',
      text: 'text-error',
      button: 'bg-error text-text-on-accent hover:bg-error/90',
      closeButton: 'text-error hover:text-error/80',
    },
    warning: {
      container: 'bg-warning-light border border-warning/20',
      icon: 'text-warning',
      text: 'text-warning',
      button: 'bg-warning text-text-on-accent hover:bg-warning/90',
      closeButton: 'text-warning hover:text-warning/80',
    },
    info: {
      container: 'bg-accent-light border border-accent/20',
      icon: 'text-accent',
      text: 'text-accent',
      button: 'bg-accent text-text-on-accent hover:bg-accent-hover',
      closeButton: 'text-accent hover:text-accent-hover',
    },
  };

  const styles = severityStyles[conflict.severity];

  return (
    <div
      className={`mb-2 px-3 py-2 rounded-lg flex items-start space-x-2 transition-all duration-300 ease-out overflow-hidden ${styles.container}`}
    >
      {/* 图标 */}
      {conflict.severity === 'critical' ? (
        <AlertCircle className={`w-4 h-4 flex-shrink-0 mt-0.5 ${styles.icon}`} />
      ) : (
        <Info className={`w-4 h-4 flex-shrink-0 mt-0.5 ${styles.icon}`} />
      )}

      {/* 消息内容 */}
      <div className="flex-1 min-w-0">
        <div className={`text-xs ${styles.text}`}>{conflict.message}</div>

        {/* 操作按钮 */}
        {(conflict.actions.switchModel || conflict.actions.removeImage) && (
          <div className="flex items-center space-x-2 mt-2">
            {conflict.actions.switchModel && onSwitchModel && (
              <button
                onClick={() => onSwitchModel(conflict.actions.switchModel!)}
                className={`px-2 py-1 text-xs rounded transition-base ${styles.button}`}
              >
                切换至 {conflict.actions.switchModel.name}
              </button>
            )}
            {conflict.actions.removeImage && onRemoveImage && (
              <button
                onClick={onRemoveImage}
                className="px-2 py-1 text-xs border border-border-default rounded hover:bg-hover transition-base"
              >
                删除图片
              </button>
            )}
          </div>
        )}
      </div>

      {/* 关闭按钮 */}
      {onClose && (
        <button onClick={onClose} className={`flex-shrink-0 ${styles.closeButton}`}>
          <X className="w-4 h-4" />
        </button>
      )}
    </div>
  );
}
