/**
 * 订阅相关类型定义
 */

/** 模型基础信息（来自后端） */
export interface ModelInfo {
  id: string;
  status: string;
}

/** 模型列表响应 */
export interface ModelListResponse {
  models: ModelInfo[];
}

/** 单个订阅记录 */
export interface SubscriptionItem {
  model_id: string;
  subscribed_at: string;
}

/** 订阅列表响应 */
export interface SubscriptionListResponse {
  subscriptions: SubscriptionItem[];
}

/** 订阅操作响应 */
export interface SubscriptionActionResponse {
  message: string;
  model_id: string;
}
