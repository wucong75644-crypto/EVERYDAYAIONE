import { useState } from 'react';
import type { SkillSummary } from '../../../services/skills';

/** Draft-only selection; never written into conversation settings or storage. */
export function useTurnSkillSelection(conversationId: string | null, enabled: boolean) {
  const [choice, setChoice] = useState<{ conversationId: string; skill: SkillSummary } | null>(null);
  const selected = enabled && choice?.conversationId === conversationId ? choice.skill : null;
  if (choice && (!enabled || choice.conversationId !== conversationId)) setChoice(null);
  return {
    selected,
    select: (skill: SkillSummary | null, id: string) => setChoice(skill ? { conversationId: id, skill } : null),
    take: () => {
      setChoice(null);
      return selected ? { skill_id: selected.skill_id, revision: selected.revision } : undefined;
    },
  };
}
