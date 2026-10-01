import { useState } from 'react';
import { supportsSkillMode, type SkillSummary, type SkillTaskMode } from '../../../services/skills';

/** Draft-only selection; never written into conversation settings or storage. */
export function useTurnSkillSelection(conversationId: string | null, enabled: boolean, taskMode: SkillTaskMode = 'smart') {
  const [choice, setChoice] = useState<{ conversationId: string; skill: SkillSummary } | null>(null);
  const selected = enabled && choice?.conversationId === conversationId && supportsSkillMode(choice.skill, taskMode) ? choice.skill : null;
  if (choice && (!enabled || choice.conversationId !== conversationId || !supportsSkillMode(choice.skill, taskMode))) setChoice(null);
  return {
    selected,
    select: (skill: SkillSummary | null, id: string) => setChoice(skill ? { conversationId: id, skill } : null),
    take: () => {
      setChoice(null);
      return selected ? { skill_id: selected.skill_id, revision: selected.revision } : undefined;
    },
  };
}
