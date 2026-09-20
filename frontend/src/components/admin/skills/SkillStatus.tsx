import { Badge } from '../../ui/Badge';
import type { SkillState } from '../../../services/skillAdmin';
import { stateLabels } from './presentation';

export function SkillStatus({ state, approved = false }: { state: SkillState | 'retired'; approved?: boolean }) {
  return <Badge variant={state === 'published' ? 'success' : state === 'in_review' ? 'warning' : 'default'}>
    <span className="mr-1.5 h-1 w-1 rounded-full bg-current" aria-hidden="true" />
    {state === 'in_review' && approved ? '审核通过' : stateLabels[state]}
  </Badge>;
}
