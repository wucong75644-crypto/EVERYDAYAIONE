/**
 * 图片上传 Hook
 *
 * 处理图片选择、上传、预览和删除（支持多图片）
 */

import { useState } from 'react';
import { uploadImage } from '../services/image';

export interface UploadedImage {
  id: string; // 唯一标识
  file: File;
  preview: string; // ObjectURL 预览（本地 blob:// URL，性能优于 base64）
  url: string | null; // 上传后的公网URL
  isUploading: boolean;
  error: string | null;
}

export function useImageUpload() {
  const [images, setImages] = useState<UploadedImage[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);

  // 文件校验常量
  const DEFAULT_MAX_FILE_SIZE = 10 * 1024 * 1024; // 10MB（默认）
  const ALLOWED_TYPES = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp'];

  /**
   * 校验单个文件
   */
  const validateFile = (file: File, maxFileSizeMB?: number): string | null => {
    const maxFileSize = maxFileSizeMB ? maxFileSizeMB * 1024 * 1024 : DEFAULT_MAX_FILE_SIZE;

    if (file.size > maxFileSize) {
      const sizeMB = maxFileSizeMB || 10;
      return `图片大小不能超过 ${sizeMB}MB`;
    }
    if (!ALLOWED_TYPES.includes(file.type)) {
      return '仅支持 JPG、PNG、WebP 格式的图片';
    }
    return null;
  };

  /**
   * 处理图片文件列表
   */
  const handleImageFiles = async (
    files: FileList | File[],
    maxImages?: number,
    maxFileSizeMB?: number
  ) => {
    const fileArray = Array.from(files);

    // 清除之前的错误
    setUploadError(null);

    // 检查数量限制
    const currentCount = images.length;
    const newCount = fileArray.length;
    const totalCount = currentCount + newCount;

    if (maxImages && totalCount > maxImages) {
      setUploadError(`最多只能上传 ${maxImages} 张图片，当前已有 ${currentCount} 张`);
      return;
    }

    // 校验所有文件
    for (const file of fileArray) {
      const error = validateFile(file, maxFileSizeMB);
      if (error) {
        setUploadError(error);
        return;
      }
    }

    // 为每个文件创建记录并开始上传
    const newImages: UploadedImage[] = fileArray.map((file) => ({
      id: `${Date.now()}-${Math.random()}`,
      file,
      preview: '', // 稍后填充
      url: null,
      isUploading: true,
      error: null,
    }));

    setImages((prev) => [...prev, ...newImages]);

    // 逐个处理图片（读取预览 + 上传）
    for (const newImage of newImages) {
      try {
        // 使用 ObjectURL（性能优于 base64，内存占用更小）
        const preview = URL.createObjectURL(newImage.file);

        // 更新预览（立即显示）
        setImages((prev) =>
          prev.map((img) => (img.id === newImage.id ? { ...img, preview } : img))
        );

        // 读取文件为 base64（仅用于上传）
        const reader = new FileReader();
        const base64 = await new Promise<string>((resolve, reject) => {
          reader.onload = () => resolve(reader.result as string);
          reader.onerror = () => reject(new Error('图片读取失败'));
          reader.readAsDataURL(newImage.file);
        });

        // 上传到服务器
        const uploadResult = await uploadImage(base64);

        // 更新URL和状态
        setImages((prev) =>
          prev.map((img) =>
            img.id === newImage.id
              ? { ...img, url: uploadResult.url, isUploading: false }
              : img
          )
        );
      } catch (error) {
        console.error('图片上传失败:', error);
        setImages((prev) =>
          prev.map((img) =>
            img.id === newImage.id
              ? { ...img, isUploading: false, error: '上传失败' }
              : img
          )
        );
      }
    }
  };

  /**
   * 处理图片选择（从文件输入框）
   */
  const handleImageSelect = async (
    e: React.ChangeEvent<HTMLInputElement>,
    maxImages?: number,
    maxFileSizeMB?: number
  ) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    await handleImageFiles(files, maxImages, maxFileSizeMB);

    // 清空 input 以允许重复选择同一文件
    e.target.value = '';
  };

  /**
   * 处理拖拽上传
   */
  const handleImageDrop = async (files: FileList, maxImages?: number, maxFileSizeMB?: number) => {
    await handleImageFiles(files, maxImages, maxFileSizeMB);
  };

  /**
   * 处理粘贴上传
   */
  const handleImagePaste = async (e: ClipboardEvent, maxImages?: number, maxFileSizeMB?: number) => {
    const items = e.clipboardData?.items;
    if (!items) return;

    const imageFiles: File[] = [];
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.type.indexOf('image') !== -1) {
        const file = item.getAsFile();
        if (file) imageFiles.push(file);
      }
    }

    if (imageFiles.length > 0) {
      e.preventDefault();
      await handleImageFiles(imageFiles, maxImages, maxFileSizeMB);
    }
  };

  /**
   * 移除单张图片
   */
  const handleRemoveImage = (imageId: string) => {
    setImages((prev) => {
      const imageToRemove = prev.find((img) => img.id === imageId);
      // 清理 ObjectURL 防止内存泄漏
      if (imageToRemove?.preview.startsWith('blob:')) {
        URL.revokeObjectURL(imageToRemove.preview);
      }
      return prev.filter((img) => img.id !== imageId);
    });
    setUploadError(null);
  };

  /**
   * 移除所有图片
   */
  const handleRemoveAllImages = () => {
    // 提取需要释放的 URL（在清空状态之前）
    const urlsToRevoke = images.map((img) => img.preview).filter((url) => url.startsWith('blob:'));

    // 清空状态，让 UI 立即响应（输入框变空）
    setImages([]);
    setUploadError(null);

    // 延迟释放内存（30秒后），确保消息列表中的图片已经渲染完成
    if (urlsToRevoke.length > 0) {
      setTimeout(() => {
        urlsToRevoke.forEach((url) => {
          URL.revokeObjectURL(url);
          console.log(`[Memory Cleanup] Revoked ObjectURL: ${url.slice(0, 50)}...`);
        });
      }, 30000); // 30秒延迟
    }
  };

  /**
   * 清除上传错误
   */
  const clearUploadError = () => {
    setUploadError(null);
  };

  // 计算派生状态
  const isUploading = images.some((img) => img.isUploading);
  const uploadedImageUrls = images
    .filter((img) => img.url !== null)
    .map((img) => img.url as string);
  const previewUrls = images.map((img) => img.preview);
  const hasImages = images.length > 0;

  return {
    images, // 所有图片记录
    uploadedImageUrls, // 已上传的图片 URL 数组（服务器 URL）
    previewUrls, // 本地预览 URL 数组（ObjectURL，用于消息显示）
    isUploading, // 是否有图片正在上传
    uploadError, // 上传错误信息
    hasImages, // 是否有图片
    handleImageSelect, // 文件选择处理
    handleImageDrop, // 拖拽上传处理
    handleImagePaste, // 粘贴上传处理
    handleRemoveImage, // 移除单张图片
    handleRemoveAllImages, // 移除所有图片
    clearUploadError, // 清除错误
  };
}
