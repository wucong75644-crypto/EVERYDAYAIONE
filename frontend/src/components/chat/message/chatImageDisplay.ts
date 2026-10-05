import type { Message } from '../../../types/message';

export function isChatImage(message: Message): boolean {
  return message.role === 'assistant' && message.generation_params?.origin === 'chat_image';
}

/** Only consecutive image children are grouped; user turns and other outputs are barriers. */
export function groupChatImages(messages: Message[]): Message[][] {
  const groups: Message[][] = [];
  for (const message of messages) {
    const last = groups.at(-1);
    if (isChatImage(message) && last && isChatImage(last[0])) last.push(message);
    else groups.push([message]);
  }
  return groups;
}

/** Remove the known submission receipt section from old image-tool replies. */
export function imagePlanText(text: string): string {
  const receipt = /(?:^|\n)[ \t]*(?:#{1,6}\s*)?(?:\*\*)?(?:这(?:两|几|\d+|[一二三四五六七八九十]+)个方案风格|现在[^\n]*(?:提交|生成)[^\n]*图片|已成功提交[^\n]*图片|✅[^\n]*图片任务|(?:本次|已)提交的任务|说明[：:]|至此[，,])/m;
  const match = receipt.exec(text);
  return (match ? text.slice(0, match.index) : text).trim();
}
