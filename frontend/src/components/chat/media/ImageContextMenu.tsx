/**
 * 图片右键上下文菜单
 *
 * 在 AI 生成的图片上右键弹出，提供：
 * - 引用：将图片引用到输入框进行编辑
 * - 复制：将图片复制到剪贴板
 * - 下载：下载图片到本地
 */

import { useEffect, useRef } from 'react';
import { Quote, Copy, Download } from 'lucide-react';
import toast from 'react-hot-toast';
import { downloadImage } from '../../../utils/downloadImage';

interface ImageContextMenuProps {
  x: number;
  y: number;
  imageUrl: string;
  messageId: string;
  closing?: boolean;
  onClose: () => void;
}

export default function ImageContextMenu({
  x,
  y,
  imageUrl,
  messageId,
  closing = false,
  onClose,
}: ImageContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);

  // 点击菜单外区域 / ESC 关闭
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };

    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleEsc);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEsc);
    };
  }, [onClose]);

  // 确保菜单不超出视口
  const adjustedPosition = adjustMenuPosition(x, y);

  const handleQuote = () => {
    window.dispatchEvent(
      new CustomEvent('chat:quote-image', {
        detail: { url: imageUrl, messageId },
      })
    );
    onClose();
  };

  const handleCopy = async () => {
    try {
      // clipboard.write 只支持 image/png，需通过 canvas 转换格式
      const img = new Image();
      img.crossOrigin = 'anonymous';
      const pngBlob = await new Promise<Blob>((resolve, reject) => {
        img.onload = () => {
          const canvas = document.createElement('canvas');
          canvas.width = img.naturalWidth;
          canvas.height = img.naturalHeight;
          const ctx = canvas.getContext('2d')!;
          ctx.drawImage(img, 0, 0);
          canvas.toBlob(
            (blob) => (blob ? resolve(blob) : reject(new Error('toBlob failed'))),
            'image/png'
          );
        };
        img.onerror = () => reject(new Error('image load failed'));
        img.src = imageUrl;
      });
      await navigator.clipboard.write([
        new ClipboardItem({ 'image/png': pngBlob }),
      ]);
      toast.success('已复制到剪贴板');
    } catch {
      // 降级：复制图片链接
      try {
        await navigator.clipboard.writeText(imageUrl);
        toast.success('已复制图片链接');
      } catch {
        toast.error('复制失败');
      }
    }
    onClose();
  };

  const handleDownload = async () => {
    try {
      await downloadImage(imageUrl, `image-${messageId}`);
    } catch {
      toast.error('下载失败');
    }
    onClose();
  };

  const items = [
    { label: '引用', icon: Quote, onClick: handleQuote, className: 'text-accent' },
    { label: '复制', icon: Copy, onClick: handleCopy, className: 'text-text-secondary' },
    { label: '下载', icon: Download, onClick: handleDownload, className: 'text-text-secondary' },
  ];

  return (
    <div
      ref={menuRef}
      className={`fixed bg-surface-card rounded-lg shadow-lg border border-border-default py-1 z-30 min-w-32 ${
        closing ? 'animate-dropdown-exit' : 'animate-dropdown-enter'
      }`}
      style={{ left: `${adjustedPosition.x}px`, top: `${adjustedPosition.y}px` }}
      onClick={(e) => e.stopPropagation()}
    >
      {items.map(({ label, icon: Icon, onClick, className }) => (
        <button
          key={label}
          onClick={onClick}
          className={`w-full px-4 py-2 text-left text-sm hover:bg-hover flex items-center gap-2 transition-base ${className}`}
        >
          <Icon className="w-4 h-4" />
          {label}
        </button>
      ))}
    </div>
  );
}

/** 调整菜单位置，避免超出视口 */
function adjustMenuPosition(x: number, y: number): { x: number; y: number } {
  const menuWidth = 140;
  const menuHeight = 130;
  const adjustedX = x + menuWidth > window.innerWidth ? window.innerWidth - menuWidth - 8 : x;
  const adjustedY = y + menuHeight > window.innerHeight ? window.innerHeight - menuHeight - 8 : y;
  return { x: adjustedX, y: adjustedY };
}
