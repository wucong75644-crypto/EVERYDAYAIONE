/** 前端灰度开关。默认开启新卡片，但只有带 ChangeSet 引用的消息会进入新路径。 */
export function isChangeSetChatUiEnabled(): boolean {
  return import.meta.env.VITE_CHANGESET_CHAT_UI !== 'false';
}
