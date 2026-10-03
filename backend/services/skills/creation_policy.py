"""Organization policy for AI-assisted Skill authoring and publication requests."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SkillCreationPolicy:
    chat_creation_enabled: bool = True
    org_submission_enabled: bool = True
    platform_submission_enabled: bool = True

    def as_targets(self, *, has_org_membership: bool) -> dict[str, bool]:
        enabled = self.chat_creation_enabled
        return {
            'personal': enabled,
            'org': enabled and has_org_membership and self.org_submission_enabled,
            'platform': enabled and self.platform_submission_enabled,
        }


POLICY_KEYS = {
    'skill_chat_creation_enabled': 'chat_creation_enabled',
    'skill_org_submission_enabled': 'org_submission_enabled',
    'skill_platform_submission_enabled': 'platform_submission_enabled',
}


def from_features(features) -> SkillCreationPolicy:
    """Missing flags preserve current behavior; malformed values fail closed."""
    if features is None:
        return SkillCreationPolicy()
    if not isinstance(features, dict):
        return SkillCreationPolicy(False, False, False)
    values = features
    normalized = {}
    for key, field in POLICY_KEYS.items():
        value = values.get(key, True)
        normalized[field] = value is True
    return SkillCreationPolicy(**normalized)


def for_organization(db, org_id: str | None) -> SkillCreationPolicy:
    """Load organization policy from trusted server storage, never request data."""
    if not org_id:
        return SkillCreationPolicy()
    response = db.table('organizations').select('status,features').eq(
        'id', org_id,
    ).maybe_single().execute()
    row = response.data if response and isinstance(response.data, dict) else None
    if not row or row.get('status') != 'active':
        return SkillCreationPolicy(False, False, False)
    return from_features(row.get('features'))
