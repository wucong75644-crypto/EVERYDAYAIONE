/**
 * 订阅相关 API
 */

import { request } from './api';
import type {
  ModelListResponse,
  SubscriptionActionResponse,
  SubscriptionListResponse,
} from '../types/subscription';

/**
 * 获取所有模型基础信息（公开接口）
 */
export async function getModels(): Promise<ModelListResponse> {
  return request({
    method: 'GET',
    url: '/models',
  });
}

/**
 * 获取当前用户已订阅的模型列表
 */
export async function getSubscriptions(): Promise<SubscriptionListResponse> {
  return request({
    method: 'GET',
    url: '/subscriptions',
  });
}

/**
 * 订阅指定模型
 */
export async function subscribeModel(modelId: string): Promise<SubscriptionActionResponse> {
  return request({
    method: 'POST',
    url: `/subscriptions/${encodeURIComponent(modelId)}`,
  });
}

/**
 * 取消订阅指定模型
 */
export async function unsubscribeModel(modelId: string): Promise<SubscriptionActionResponse> {
  return request({
    method: 'DELETE',
    url: `/subscriptions/${encodeURIComponent(modelId)}`,
  });
}
