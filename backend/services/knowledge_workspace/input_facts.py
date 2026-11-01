"""Host-owned knowledge routing facts, separate from rendered model messages.

Only the validated input snapshot creates an exemption. Checkpoints preserve it
across rendering/replay; real user changes and evidence can only narrow it.
"""
from dataclasses import asdict, dataclass, replace

from .scope_selection import has_knowledge_evidence

CHECKPOINT_KEY = '_knowledge_input_facts'


@dataclass(frozen=True)
class KnowledgeInputFacts:
    conversation_id: str
    input_message_id: str
    org_id: str | None
    question: str
    kinds: tuple[str, ...]
    history_evidence: bool
    changed: bool = False
    knowledge_used: bool = False
    version: int = 1

    @classmethod
    def from_snapshot(cls, snapshot, content):
        if not isinstance(content, list) or not all(isinstance(p, dict) for p in content):
            return None
        history = list(snapshot.history_messages)
        if snapshot.summary_prompt:
            history.append({'role': 'system', 'content': snapshot.summary_prompt})
        return cls(str(snapshot.anchor.conversation_id), str(snapshot.anchor.input_message_id),
            snapshot.anchor.org_id,
            '\n'.join(p['text'] for p in content if p.get('type') == 'text' and isinstance(p.get('text'), str)),
            tuple(p.get('type', 'unknown') for p in content), has_knowledge_evidence(history))

    def checkpoint(self, *, changed=False, knowledge_used=False):
        value = asdict(replace(self, changed=self.changed or changed,
                              knowledge_used=self.knowledge_used or knowledge_used))
        value['kinds'] = list(self.kinds)
        return value


def restore_facts(payload, anchor):
    """Followups keep the new window nested; never use another input's exemption.

    Old opaque checkpoint fields remain intact. A prior window's real evidence
    remains an obligation even when the new window is otherwise self-contained.
    Missing/unknown schemas intentionally return no exemption.
    """
    if anchor is None or not isinstance(payload, dict):
        return None
    inherited_evidence = False
    inherited_change = False
    current = payload
    for _ in range(32):
        raw = current.get(CHECKPOINT_KEY)
        if (not isinstance(raw, dict) or type(raw.get('version')) is not int or raw['version'] != 1
                or raw.get('conversation_id') != str(anchor.conversation_id)
                or raw.get('org_id') != anchor.org_id
                or not isinstance(raw.get('input_message_id'), str)
                or not isinstance(raw.get('question'), str) or not isinstance(raw.get('kinds'), list)
                or not all(isinstance(k, str) for k in raw['kinds'])
                or any(type(raw.get(k)) is not bool for k in ('history_evidence', 'changed', 'knowledge_used'))):
            # A followup cannot make unknown prior evidence disappear merely
            # because its newly created window has structured facts.
            return None
        inherited_evidence |= raw['history_evidence'] or raw['knowledge_used']
        changes = current.get('_task_user_changes')
        items = changes.get('items', []) if isinstance(changes, dict) else []
        # This ledger is committed before a directive reaches the model. Both
        # pending changes and older amendments keep the conservative boundary.
        inherited_change |= raw['changed'] or bool(items)
        if raw['input_message_id'] == str(anchor.input_message_id):
            return KnowledgeInputFacts(str(anchor.conversation_id), str(anchor.input_message_id),
                anchor.org_id, raw['question'], tuple(raw['kinds']), inherited_evidence,
                changed=inherited_change, knowledge_used=raw['knowledge_used'])
        current = current.get('_task_followup_input_checkpoint')
        if not isinstance(current, dict):
            return None
    return None
