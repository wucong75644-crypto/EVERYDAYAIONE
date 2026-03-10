/**
 * 媒体模型配置
 *
 * 包含图像生成和视频生成模型的定义
 */

import { type UnifiedModel } from './modelTypes';

// 图像模型
export const IMAGE_MODELS: UnifiedModel[] = [
  {
    id: 'google/nano-banana',
    name: 'Nano Banana',
    type: 'image',
    description: '基础文生图',
    capabilities: {
      textToImage: true,
      imageEditing: false,
      imageToVideo: false,
      textToVideo: false,
      vqa: false,
      videoQA: false,
    },
    credits: 4,
  },
  {
    id: 'google/nano-banana-edit',
    name: 'Nano Banana Edit',
    type: 'image',
    description: '图像编辑',
    capabilities: {
      textToImage: false,
      imageEditing: true,
      imageToVideo: false,
      textToVideo: false,
      vqa: false,
      videoQA: false,
      maxImages: 10,
      maxFileSize: 10,
    },
    credits: 6,
  },
  {
    id: 'nano-banana-pro',
    name: 'Nano Banana Pro',
    type: 'image',
    description: '高级文生图/图生图',
    capabilities: {
      textToImage: true,
      imageEditing: true,
      imageToVideo: false,
      textToVideo: false,
      vqa: false,
      videoQA: false,
      maxImages: 8,
      maxFileSize: 30,
    },
    credits: { '1K': 18, '2K': 18, '4K': 24 },
    supportsResolution: true,
  },
];

// 视频模型
export const VIDEO_MODELS: UnifiedModel[] = [
  {
    id: 'sora-2-text-to-video',
    name: 'Sora 2 Text-to-Video',
    type: 'video',
    description: '文生视频',
    capabilities: {
      textToImage: false,
      imageEditing: false,
      imageToVideo: false,
      textToVideo: true,
      vqa: false,
      videoQA: false,
    },
    credits: 30,
    videoPricing: {
      '10': 30,
      '15': 45,
    },
  },
  {
    id: 'sora-2-image-to-video',
    name: 'Sora 2 Image-to-Video',
    type: 'video',
    description: '图生视频',
    capabilities: {
      textToImage: false,
      imageEditing: false,
      imageToVideo: true,
      textToVideo: false,
      vqa: false,
      videoQA: false,
      maxImages: 1,
    },
    credits: 30,
    videoPricing: {
      '10': 30,
      '15': 45,
    },
  },
  {
    id: 'sora-2-pro-storyboard',
    name: 'Sora 2 Pro Storyboard',
    type: 'video',
    description: '专业故事板',
    capabilities: {
      textToImage: false,
      imageEditing: false,
      imageToVideo: false,
      textToVideo: true,
      vqa: false,
      videoQA: false,
    },
    credits: 150,
    videoPricing: {
      '10': 150,
      '15': 270,
      '25': 270,
    },
  },
];
