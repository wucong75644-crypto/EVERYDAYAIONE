/**
 * 图片右键上下文菜单
 *
 * 在图片上右键弹出：
 * - 引用：把图片引用到输入框进行编辑
 * - 复制：把图片复制到剪贴板
 * - 下载：把图片下载到本地
 *
 * 壳逻辑（位置/ESC/外部关闭/样式）走 BaseContextMenu，这里只负责拼业务回调。
 */

import { Quote, Copy, Download } from 'lucide-react';
import toast from 'react-hot-toast';
import { downloadImage } from '../../../utils/downloadImage';
import BaseContextMenu, { type ContextMenuItem } from '../menus/BaseContextMenu';

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
  const handleQuote = () => {
    window.dispatchEvent(
      new CustomEvent('chat:quote-image', {
        detail: { url: imageUrl, messageId },
      }),
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
            'image/png',
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

  const items: ContextMenuItem[] = [
    { label: '引用', icon: Quote, onClick: handleQuote, tone: 'accent' },
    { label: '复制', icon: Copy, onClick: handleCopy, tone: 'secondary' },
    { label: '下载', icon: Download, onClick: handleDownload, tone: 'secondary' },
  ];

  return <BaseContextMenu x={x} y={y} items={items} closing={closing} onClose={onClose} />;
}
