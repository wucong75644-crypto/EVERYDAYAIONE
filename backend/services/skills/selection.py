"""Public manual intent contains identity only, never authority or instructions."""

from services.skills.contracts import Contract, RevisionKey, SkillKey


class SkillSelection(Contract):
    skill_id: SkillKey
    revision: RevisionKey
