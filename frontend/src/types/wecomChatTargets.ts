/**
 * 企微聊天目标管理类型定义
 *
 * 后端路由: backend/api/routes/wecom_chat_targets.py
 */

export interface WecomGroup {
  id: string;
  chatid: string;
  chat_type: 'group';
  chat_name: string | null;  // 企微 API 拿不到，靠管理员手动标注
  last_active: string;
  first_seen: string;
  message_count: number;
  is_active: boolean;
}

export interface UpdateChatNameDto {
  chat_name: string;
}
