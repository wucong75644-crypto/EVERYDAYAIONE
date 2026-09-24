import { ApiRequestError } from '../../../services/api';
import type { SkillAssetDraft } from '../../../services/skillAdmin';

export const uploadMessages: Record<string, string> = {
  SKILL_UPLOAD_FORMAT: '文件格式暂不支持。请上传 DOCX、PDF、XLSX、TXT、Markdown、CSV 或 JSON；旧版 DOC、XLS 请先另存为 DOCX、XLSX。',
  SKILL_UPLOAD_FILENAME_INVALID: '文件名过长或含有不支持的字符，请重命名后上传。',
  SKILL_UPLOAD_TOO_LARGE: '单个附件不能超过 2 MB，请缩小文件后重新上传。',
  SKILL_UPLOAD_PACKAGE_LIMIT: '最多上传 16 份附件，原文件合计不能超过 8 MB，读取文字合计不能超过 256 KiB。请减少附件后重试。',
  SKILL_UPLOAD_TEXT_TOO_LARGE: '文件文字超过附件容量（64 KiB），请拆分或精简后上传。',
  SKILL_UPLOAD_NO_TEXT: '文件中没有可读取的文字。扫描版 PDF 暂不支持，请换成可复制文字的 PDF 或 Word 文档。',
  SKILL_UPLOAD_ENCODING: '文本文件不是 UTF-8 编码，请另存为 UTF-8 后上传。',
  SKILL_UPLOAD_ENCRYPTED: '文件有密码保护，请解除保护后上传。',
  SKILL_UPLOAD_COMPLEXITY_LIMIT: '文件过于复杂，暂时无法读取。请精简内容或另存为文本后上传。',
  SKILL_UPLOAD_INVALID: '文件无法读取，请检查是否损坏、加密或含宏，再另存为普通文档上传。',
  SKILL_UPLOAD_BUSY: '附件读取服务正在忙，请稍后重新上传。',
};

function reject(code: string): never { throw new ApiRequestError(code, uploadMessages[code], 422); }

const sourceBytes = (asset: SkillAssetDraft) => asset.source
  ? asset.source.base64.length * 3 / 4 - (asset.source.base64.match(/=+$/)?.[0].length || 0) : 0;

export function validateUploadSelection(files: File[], assets: SkillAssetDraft[]) {
  for (const file of files) {
    if (!/\.(docx|pdf|xlsx|txt|md|csv|json)$/i.test(file.name)) reject('SKILL_UPLOAD_FORMAT');
    if (file.size > 2 * 1024 * 1024) reject('SKILL_UPLOAD_TOO_LARGE');
    if (!file.size) reject('SKILL_UPLOAD_NO_TEXT');
  }
  if (assets.length + files.length > 16 || assets.reduce((n, a) => n + sourceBytes(a), 0)
    + files.reduce((n, f) => n + f.size, 0) > 8 * 1024 * 1024) reject('SKILL_UPLOAD_PACKAGE_LIMIT');
}

export function validateUploadedAssets(assets: SkillAssetDraft[]) {
  if (assets.length > 16 || assets.reduce((n, a) => n + new TextEncoder().encode(a.content).length, 0) > 262144
    || assets.reduce((n, a) => n + sourceBytes(a), 0) > 8 * 1024 * 1024) reject('SKILL_UPLOAD_PACKAGE_LIMIT');
}
