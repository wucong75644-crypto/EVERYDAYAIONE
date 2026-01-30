/**
 * 任务配置常量
 */

/** 图片生成任务超时时间（毫秒） */
export const IMAGE_TASK_TIMEOUT = 10 * 60 * 1000; // 10分钟

/** 视频生成任务超时时间（毫秒） */
export const VIDEO_TASK_TIMEOUT = 30 * 60 * 1000; // 30分钟

/** 图片任务轮询间隔（毫秒） */
export const IMAGE_POLL_INTERVAL = 2000; // 2秒

/** 视频任务轮询间隔（毫秒） */
export const VIDEO_POLL_INTERVAL = 5000; // 5秒

/** 任务恢复时的错开延迟（毫秒） */
export const TASK_RESTORE_STAGGER_DELAY = 200;
