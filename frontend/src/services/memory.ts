/**
 * 记忆功能 API 服务
 */

import { request } from './api';

// ============================================================
// 类型定义
// ============================================================

/** 记忆元数据 */
export interface MemoryMetadata {
  source: 'auto' | 'manual';
  conversation_id: string | null;
}

/** 单条记忆 */
export interface MemoryItem {
  id: string;
  memory: string;
  metadata: MemoryMetadata;
  created_at: string | null;
  updated_at: string | null;
}

/** 记忆列表响应 */
export interface MemoryListResponse {
  memories: MemoryItem[];
  total: number;
}

/** 添加记忆响应 */
export interface MemoryAddResponse {
  memories: MemoryItem[];
  count: number;
}

/** 更新记忆响应 */
export interface MemoryUpdateResponse {
  id: string;
  memory: string;
  updated_at: string | null;
}

/** 记忆设置 */
export interface MemorySettings {
  memory_enabled: boolean;
  retention_days: number;
  updated_at: string | null;
}

// ============================================================
// API 函数
// ============================================================

/**
 * 获取记忆列表
 */
export async function getMemories(): Promise<MemoryListResponse> {
  return request<MemoryListResponse>({
    method: 'GET',
    url: '/memories',
    timeout: 60000, // Mem0 pgvector 查询可能较慢
  });
}

/**
 * 添加记忆
 */
export async function addMemory(content: string): Promise<MemoryAddResponse> {
  return request<MemoryAddResponse>({
    method: 'POST',
    url: '/memories',
    data: { content },
    timeout: 60000, // Mem0 LLM 提取 + 嵌入生成较慢
  });
}

/**
 * 更新记忆
 */
export async function updateMemory(
  id: string,
  content: string
): Promise<MemoryUpdateResponse> {
  return request<MemoryUpdateResponse>({
    method: 'PUT',
    url: `/memories/${id}`,
    data: { content },
    timeout: 60000,
  });
}

/**
 * 删除记忆
 */
export async function deleteMemory(id: string): Promise<void> {
  return request<void>({
    method: 'DELETE',
    url: `/memories/${id}`,
    timeout: 60000,
  });
}

/**
 * 清空所有记忆
 */
export async function deleteAllMemories(): Promise<void> {
  return request<void>({
    method: 'DELETE',
    url: '/memories',
    timeout: 60000,
  });
}

/**
 * 获取记忆设置
 */
export async function getMemorySettings(): Promise<MemorySettings> {
  return request<MemorySettings>({
    method: 'GET',
    url: '/memories/settings',
    timeout: 60000,
  });
}

/**
 * 更新记忆设置
 */
export async function updateMemorySettings(
  data: Partial<Pick<MemorySettings, 'memory_enabled' | 'retention_days'>>
): Promise<MemorySettings> {
  return request<MemorySettings>({
    method: 'PUT',
    url: '/memories/settings',
    data,
    timeout: 60000,
  });
}
