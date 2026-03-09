/**
 * 渲染指令类型定义
 *
 * 大脑通过 generation_params._render 发送渲染指令，
 * 前端按指令选择组件、配置占位符和布局。
 *
 * 优先级链：_render.xxx > RENDER_CONFIG[type] > 硬编码兜底
 */

// ============================================================
// 消息级渲染指令（Phase 2）
// ============================================================

/** 大脑发送的渲染指令 */
export interface RenderInstruction {
  /** 组件名（Phase 3 实现） */
  component?: RegisteredComponent | string;

  /** 覆盖占位符文字 */
  placeholder_text?: string;

  /** 覆盖完成后气泡文字 */
  bubble_text?: string;

  /** 布局提示（Phase 3 实现） */
  layout?: {
    columns?: number;
    aspect_ratio?: string;
  };
}

// ============================================================
// 块级渲染指令（Future Phase 预留）
// ============================================================

/** 块级渲染指令：一条消息内多个组件按顺序渲染 */
export interface RenderBlock {
  /** 组件名 */
  component: RegisteredComponent | string;
  /** 对应 message.content[] 的索引 */
  content_indices?: number[];
  /** 组件配置 */
  config?: Record<string, unknown>;
}

// ============================================================
// 组件注册表类型
// ============================================================

/** 已注册的渲染组件名 */
export type RegisteredComponent =
  | 'image_grid'       // AiImageGrid — AI 多图网格
  | 'image_single'     // AiGeneratedImage — AI 单图
  | 'image_gallery'    // UserImageGallery — 用户图片
  | 'video_player'     // 视频播放器
  | 'text_bubble'      // Markdown 文字气泡
  | 'audio_player'     // 音频播放器（预留）
  | 'code_editor';     // 代码编辑器（预留）

/** type → 默认组件映射（大脑未指定 component 时使用） */
export const DEFAULT_COMPONENT: Record<string, RegisteredComponent> = {
  image: 'image_grid',
  video: 'video_player',
  chat: 'text_bubble',
  audio: 'audio_player',
  code: 'code_editor',
};
