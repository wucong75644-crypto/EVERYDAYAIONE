"""Import adapted methods as normal admin drafts, never bypass review/publish."""

import json
from pathlib import Path
from core.db_scope import DatabaseScope, DatabaseAccessKind
from services.skills.repository import SkillRepository
from services.skills.authoring import SkillAuthoring
from services.skills.authoring_contracts import CreateSkill

BUNDLE = Path(__file__).resolve().parents[2] / "data/social_ops_skills"


def bundle_specs():
    return [
        CreateSkill.model_validate_json(p.read_text())
        for p in sorted(BUNDLE.glob("easel-*.json"))
    ]


def install_drafts(db, scope, settings):
    admin_scope = DatabaseScope(
        actor_user_id=scope.actor_user_id,
        org_id=scope.org_id,
        access_kind=DatabaseAccessKind.RUNTIME_ADMIN,
    )
    repo = SkillRepository(db.pool, admin_scope, owner_scope="org")
    authoring = SkillAuthoring(repo, settings)
    results = []
    for spec in bundle_specs():
        with repo._cursor() as c:
            c.execute(
                "SELECT id FROM skill_packages WHERE skill_key=%s AND org_id=%s AND scope_kind='org'",
                (spec.skill_key, scope.org_id),
            )
            existing = c.fetchone()
        if existing:
            results.append(
                {
                    "skill_key": spec.skill_key,
                    "id": str(existing["id"]),
                    "status": "existing",
                }
            )
        else:
            results.append(
                {
                    "skill_key": spec.skill_key,
                    "draft": authoring.create(spec),
                    "status": "draft_requires_review",
                }
            )
    return results
