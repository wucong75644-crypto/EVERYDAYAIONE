/**
 * 模型详情抽屉面板
 *
 * 右侧滑入，展示模型完整信息、能力、规格、费用和操作按钮。
 */

import { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { X, ExternalLink, Loader2 } from 'lucide-react';
import type { UnifiedModel } from '../../constants/models';

/** 完整能力标签映射 */
const ALL_CAPABILITY_TAGS: { key: string; check: (m: UnifiedModel) => boolean; label: string }[] = [
  { key: 'text', check: (m) => m.type === 'chat', label: '📝 文本对话' },
  { key: 'vqa', check: (m) => !!m.capabilities.vqa, label: '🖼️ 图片理解' },
  { key: 'audio', check: (m) => !!m.capabilities.audioInput, label: '🎤 音频输入' },
  { key: 'pdf', check: (m) => !!m.capabilities.pdfInput, label: '📄 PDF 文档' },
  { key: 'tools', check: (m) => !!m.capabilities.functionCalling, label: '🔧 工具调用' },
  { key: 'json', check: (m) => !!m.capabilities.structuredOutput, label: '📊 JSON 输出' },
  { key: 'stream', check: (m) => !!m.capabilities.streamingResponse, label: '⚡ 流式响应' },
  { key: 'thinking', check: (m) => !!m.capabilities.thinkingEffort, label: '🧠 推理调节' },
  { key: 'textToImage', check: (m) => !!m.capabilities.textToImage, label: '🎨 文生图' },
  { key: 'imageEdit', check: (m) => !!m.capabilities.imageEditing, label: '✏️ 图片编辑' },
  { key: 'textToVideo', check: (m) => !!m.capabilities.textToVideo, label: '🎬 文生视频' },
  { key: 'imageToVideo', check: (m) => !!m.capabilities.imageToVideo, label: '🎞️ 图生视频' },
];

/** 格式化 token 数量 */
function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return String(n);
}

interface ModelDetailDrawerProps {
  model: UnifiedModel | null;
  isOpen: boolean;
  onClose: () => void;
  isAuthenticated: boolean;
  isSubscribed: boolean;
  isSubscribing: boolean;
  onSubscribe: (modelId: string) => void;
  onUnsubscribe: () => void;
  onOpenAuth: (mode: 'login' | 'register') => void;
}

export default function ModelDetailDrawer({
  model,
  isOpen,
  onClose,
  isAuthenticated,
  isSubscribed,
  isSubscribing,
  onSubscribe,
  onUnsubscribe,
  onOpenAuth,
}: ModelDetailDrawerProps) {
  const navigate = useNavigate();
  const [isClosing, setIsClosing] = useState(false);
  const [shouldRender, setShouldRender] = useState(false);

  useEffect(() => {
    if (isOpen) {
      setShouldRender(true);
      setIsClosing(false);
      document.body.style.overflow = 'hidden';
    } else if (shouldRender) {
      setIsClosing(true);
      const timer = setTimeout(() => {
        setShouldRender(false);
        setIsClosing(false);
      }, 150);
      return () => clearTimeout(timer);
    }
    return () => {
      document.body.style.overflow = '';
    };
  }, [isOpen, shouldRender]);

  // 组件卸载时恢复 body 滚动
  useEffect(() => {
    return () => {
      document.body.style.overflow = '';
    };
  }, []);

  const handleClose = useCallback(() => {
    document.body.style.overflow = '';
    onClose();
  }, [onClose]);

  // ESC 关闭
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [isOpen, handleClose]);

  if (!shouldRender || !model) return null;

  const capabilities = ALL_CAPABILITY_TAGS.filter((tag) => tag.check(model));
  const cap = model.capabilities;

  /** 规格参数列表 */
  const specs: { label: string; value: string }[] = [];
  if (cap.maxContextTokens) {
    specs.push({ label: '上下文长度', value: `${formatTokens(cap.maxContextTokens)} tokens` });
  }
  if (cap.maxImages) {
    specs.push({ label: '最大图片数', value: `${cap.maxImages} 张` });
  }
  if (cap.maxFileSize) {
    specs.push({ label: '图片限制', value: `≤ ${cap.maxFileSize}MB` });
  }
  if (cap.maxAudioSize) {
    specs.push({ label: '音频限制', value: `≤ ${cap.maxAudioSize}MB` });
  }
  if (cap.maxVideoSize) {
    specs.push({ label: '视频限制', value: `≤ ${cap.maxVideoSize}MB` });
  }
  if (cap.maxPDFSize) {
    specs.push({ label: 'PDF 限制', value: `≤ ${cap.maxPDFSize}MB` });
  }

  const handleGoChat = () => {
    handleClose();
    navigate(`/chat?model=${encodeURIComponent(model.id)}`);
  };

  const handleSubscribeClick = () => {
    onSubscribe(model.id);
  };

  const handleRegister = () => {
    handleClose();
    onOpenAuth('register');
  };

  const handleLogin = () => {
    handleClose();
    onOpenAuth('login');
  };

  return (
    <div className="fixed inset-0 z-50">
      {/* 遮罩 */}
      <div
        className={`absolute inset-0 bg-black/40 ${isClosing ? 'animate-backdropExit' : 'animate-backdropEnter'}`}
        onClick={handleClose}
        aria-hidden="true"
      />

      {/* 面板 */}
      <div
        className={`fixed right-0 top-0 h-full w-full sm:w-[400px] bg-white shadow-2xl flex flex-col ${
          isClosing ? 'animate-drawerSlideOut' : 'animate-drawerSlideIn'
        }`}
        role="dialog"
        aria-modal="true"
        aria-label={model.name}
      >
        {/* 头部 */}
        <div className="flex items-center justify-between px-6 py-5 border-b border-gray-200">
          <h2 className="text-xl font-bold text-gray-900 truncate pr-4">{model.name}</h2>
          <button
            onClick={handleClose}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors shrink-0"
            aria-label="关闭"
          >
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        {/* 内容区（可滚动） */}
        <div className="flex-1 overflow-y-auto px-6 py-5">
          {/* 描述 */}
          <p className="text-sm text-gray-600">{model.description}</p>

          {/* 能力标签 */}
          {capabilities.length > 0 && (
            <>
              <h3 className="text-sm font-semibold text-gray-800 mt-6 mb-3">模型能力</h3>
              <div className="flex flex-wrap gap-2">
                {capabilities.map((tag) => (
                  <span
                    key={tag.key}
                    className="inline-flex items-center px-2.5 py-1 rounded-lg bg-blue-50 text-blue-700 text-sm"
                  >
                    {tag.label}
                  </span>
                ))}
              </div>
            </>
          )}

          {/* 规格参数 */}
          {specs.length > 0 && (
            <>
              <h3 className="text-sm font-semibold text-gray-800 mt-6 mb-3">规格参数</h3>
              <div className="space-y-2.5">
                {specs.map((spec) => (
                  <div key={spec.label} className="flex justify-between items-center">
                    <span className="text-sm text-gray-500">{spec.label}</span>
                    <span className="text-sm font-medium text-gray-900">{spec.value}</span>
                  </div>
                ))}
              </div>
            </>
          )}

          {/* 费用 */}
          <h3 className="text-sm font-semibold text-gray-800 mt-6 mb-3">费用</h3>
          {renderCredits(model)}
        </div>

        {/* 底部操作区 */}
        <div className="px-6 py-4 border-t border-gray-200 bg-white">
          {!isAuthenticated ? (
            /* 未登录 */
            <div className="text-center">
              <p className="text-sm text-gray-500 mb-3">登录后即可使用，注册送100积分</p>
              <button
                onClick={handleRegister}
                className="w-full bg-blue-600 text-white py-2.5 rounded-lg font-medium hover:bg-blue-700 transition-colors"
              >
                立即注册
              </button>
              <button
                onClick={handleLogin}
                className="text-sm text-blue-600 hover:text-blue-700 mt-2 block mx-auto"
              >
                已有账号？登录
              </button>
            </div>
          ) : !isSubscribed ? (
            /* 已登录 + 未订阅 */
            <div className="text-center">
              <p className="text-sm text-gray-500 mb-3">请先订阅才能使用该模型</p>
              <button
                onClick={handleSubscribeClick}
                disabled={isSubscribing}
                className="w-full bg-blue-600 text-white py-2.5 rounded-lg font-medium hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isSubscribing ? (
                  <span className="inline-flex items-center gap-2">
                    <Loader2 className="w-4 h-4 animate-spin" />
                    订阅中...
                  </span>
                ) : (
                  '订阅此模型'
                )}
              </button>
            </div>
          ) : (
            /* 已登录 + 已订阅 */
            <div className="text-center">
              <button
                onClick={handleGoChat}
                className="w-full bg-blue-600 text-white py-2.5 rounded-lg font-medium hover:bg-blue-700 transition-colors inline-flex items-center justify-center gap-2"
              >
                <ExternalLink className="w-4 h-4" />
                前往聊天页
              </button>
              <button
                onClick={onUnsubscribe}
                className="text-sm text-red-500 hover:text-red-600 mt-3 transition-colors"
              >
                取消订阅
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** 渲染费用信息 */
function renderCredits(model: UnifiedModel) {
  // 单价模型（聊天模型）
  if (typeof model.credits === 'number') {
    const isFree = model.credits === 0;
    return (
      <div className="flex justify-between items-center">
        <span className="text-sm text-gray-500">单次消耗</span>
        <span className={`text-sm font-medium ${isFree ? 'text-green-600' : 'text-gray-900'}`}>
          {isFree ? '免费' : `${model.credits} 积分/次`}
        </span>
      </div>
    );
  }

  // 分辨率价格表（图片模型）
  const entries = Object.entries(model.credits);

  // 视频模型有 videoPricing
  if (model.videoPricing) {
    const videoEntries = Object.entries(model.videoPricing);
    return (
      <div className="border border-gray-100 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-gray-50">
              <th className="text-left px-3 py-2 font-medium text-gray-600">时长</th>
              <th className="text-right px-3 py-2 font-medium text-gray-600">费用</th>
            </tr>
          </thead>
          <tbody>
            {videoEntries.map(([duration, cost]) => (
              <tr key={duration} className="border-t border-gray-100">
                <td className="px-3 py-2 text-gray-700">{duration}秒</td>
                <td className="px-3 py-2 text-right text-gray-900 font-medium">{cost} 积分</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  // 图片模型分辨率价格表
  return (
    <div className="border border-gray-100 rounded-lg overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-gray-50">
            <th className="text-left px-3 py-2 font-medium text-gray-600">分辨率</th>
            <th className="text-right px-3 py-2 font-medium text-gray-600">费用</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([res, cost]) => (
            <tr key={res} className="border-t border-gray-100">
              <td className="px-3 py-2 text-gray-700">{res}</td>
              <td className="px-3 py-2 text-right text-gray-900 font-medium">{cost} 积分</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
