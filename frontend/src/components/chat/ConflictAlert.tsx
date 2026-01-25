/**
 * 冲突警告组件
 *
 * 显示模型与当前状态的冲突信息，并提供解决方案
 */

import { type ModelConflict } from '../../utils/modelConflict';
import { type UnifiedModel } from '../../constants/models';

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

  // 根据严重程度确定样式
  const severityStyles = {
    critical: {
      container: 'bg-red-50 border border-red-200',
      icon: 'text-red-600',
      text: 'text-red-800',
      button: 'bg-red-600 text-white hover:bg-red-700',
      closeButton: 'text-red-600 hover:text-red-800',
    },
    warning: {
      container: 'bg-yellow-50 border border-yellow-200',
      icon: 'text-yellow-600',
      text: 'text-yellow-800',
      button: 'bg-yellow-600 text-white hover:bg-yellow-700',
      closeButton: 'text-yellow-600 hover:text-yellow-800',
    },
    info: {
      container: 'bg-blue-50 border border-blue-200',
      icon: 'text-blue-600',
      text: 'text-blue-800',
      button: 'bg-blue-600 text-white hover:bg-blue-700',
      closeButton: 'text-blue-600 hover:text-blue-800',
    },
  };

  const styles = severityStyles[conflict.severity];

  return (
    <div
      className={`mb-2 px-3 py-2 rounded-lg flex items-start space-x-2 transition-all duration-300 ease-out overflow-hidden ${styles.container}`}
    >
      {/* 图标 */}
      <svg className={`w-4 h-4 flex-shrink-0 mt-0.5 ${styles.icon}`} fill="currentColor" viewBox="0 0 20 20">
        {conflict.severity === 'critical' ? (
          <path
            fillRule="evenodd"
            d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z"
            clipRule="evenodd"
          />
        ) : (
          <path
            fillRule="evenodd"
            d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z"
            clipRule="evenodd"
          />
        )}
      </svg>

      {/* 消息内容 */}
      <div className="flex-1 min-w-0">
        <div className={`text-xs ${styles.text}`}>{conflict.message}</div>

        {/* 操作按钮 */}
        {(conflict.actions.switchModel || conflict.actions.removeImage) && (
          <div className="flex items-center space-x-2 mt-2">
            {conflict.actions.switchModel && onSwitchModel && (
              <button
                onClick={() => onSwitchModel(conflict.actions.switchModel!)}
                className={`px-2 py-1 text-xs rounded transition-colors ${styles.button}`}
              >
                切换至 {conflict.actions.switchModel.name}
              </button>
            )}
            {conflict.actions.removeImage && onRemoveImage && (
              <button
                onClick={onRemoveImage}
                className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50 transition-colors"
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
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      )}
    </div>
  );
}
