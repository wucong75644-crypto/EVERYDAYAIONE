import type { RequirementAssistResult } from '../types/ecomRequirement';

export const REQUIREMENT_MAX_LENGTH = 10_000;

export function draftValidationError(draft: RequirementAssistResult): string | null {
  if (!draft.product_description.trim()) return '请填写产品细节与规格';
  if (draft.selling_points.some(point => !point.feature.trim() || !point.benefit.trim())) {
    return '请补全卖点特点和使用价值，或删除空白卖点';
  }
  if (draft.creative_requirements.some(item => !item.topic.trim() || !item.text.trim())) {
    return '请补全背景与风格要求，或删除空白要求';
  }
  return null;
}

export function formatRequirementDraft(
  draft: RequirementAssistResult,
  original: string,
  supplements: string[],
  skippedQuestions: string[],
  currentSupplement = '',
): string {
  const sections: string[] = [];
  if (original.trim()) sections.push('## 用户原始输入（留档）\n' + original);
  if (supplements.length) sections.push('## 用户补充（按时间顺序）\n' + supplements.join('\n\n'));
  sections.push('## 当前人工确认资料（与历史原文有差异时，以本节为准）\n' + draft.product_description);
  if (draft.selling_points.length) {
    sections.push('### 卖点\n' + draft.selling_points.map(point =>
      '- ' + point.feature + '：' + point.benefit + (point.benefit_basis === 'inferred' ? '（AI合理推断，经用户审核采用）' : ''),
    ).join('\n'));
  }
  if (draft.creative_requirements.length) {
    sections.push('### 背景与风格要求\n' + draft.creative_requirements.map(item =>
      '- ' + item.topic + '：' + item.text + (item.basis === 'suggested' ? '（AI建议，经用户审核采用）' : ''),
    ).join('\n'));
  }
  if (skippedQuestions.length) {
    sections.push('### 补充偏好（以下问题曾跳过；已确认资料优先，未确认的信息保持未知，不作为产品事实）\n' + skippedQuestions.map(item => '- ' + item).join('\n'));
  }
  if (currentSupplement.trim()) {
    sections.push('## 本次客户补充（最新内容，与上文有差异时以此为准）\n' + currentSupplement);
  }
  return sections.join('\n\n');
}

export function buildSupplementText(draft: RequirementAssistResult, answers: Record<string, string>, skipped: string[], text: string): string {
  return [
    ...draft.supplement_questions.filter(item => !skipped.includes(item.question) && answers[item.question]?.trim())
      .map(item => item.question + '\n' + answers[item.question]),
    ...(text.trim() ? [text] : []),
  ].join('\n\n');
}
