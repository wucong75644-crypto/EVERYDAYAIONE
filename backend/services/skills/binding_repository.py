"""Fixed conversation configuration. This writer is never a model tool."""

from uuid import UUID

from services.skills.contracts import SkillError
from services.skills.repository import SkillRepository
from services.skills.resolver import SkillCandidate


class SkillBindingRepository(SkillRepository):
    def bindings(self, conversation_id: UUID) -> list[dict]:
        # Keep revoked/disabled rows visible: a binding must never disappear from
        # the runtime silently and thereby remove its tool restriction.
        with self._cursor() as cursor:
            cursor.execute("""SELECT b.id, b.created_at, b.created_by,
                    p.id AS package_id, p.skill_key, p.org_id AS package_org_id,
                    p.owner_user_id AS package_user_id,
                    p.scope_kind, a.org_id AS assignment_org_id,
                    (p.scope_kind='platform' AND a.org_id IS NULL AND a.package_id IS NOT NULL) AS global_assignment,
                    COALESCE(a.priority, 0) AS priority, r.revision,
                    r.summary AS description, r.catalog_metadata,
                    ((p.scope_kind = 'personal' AND p.owner_user_id = %s::uuid AND r.status = 'published')
                     OR (p.scope_kind IN ('org','platform') AND COALESCE(a.enabled, FALSE)
                         AND r.status = 'published')) AS available
                FROM public.conversation_skill_bindings b
                JOIN public.skill_packages p ON p.id = b.package_id
                JOIN public.skill_revisions r ON r.id = b.revision_id AND r.package_id = b.package_id
                LEFT JOIN LATERAL (SELECT * FROM public.skill_assignments
                    WHERE package_id=b.package_id AND (org_id=b.org_id OR org_id IS NULL)
                    ORDER BY org_id NULLS LAST LIMIT 1) a ON true
                WHERE b.conversation_id = %s AND b.org_id IS NOT DISTINCT FROM %s::uuid
                ORDER BY b.created_at, b.id""", (self.scope.actor_user_id, conversation_id, self.scope.org_id))
            return cursor.fetchall()

    @staticmethod
    def candidate(row: dict) -> SkillCandidate:
        return SkillCandidate.model_validate({key: row[key] for key in SkillCandidate.model_fields if key in row})

    def add_binding(self, conversation_id: UUID, candidate: SkillCandidate) -> UUID:
        self._require_admin()
        with self._cursor() as cursor:
            self.lock_package_write(cursor, candidate.package_id)
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                           ('skill-binding:' + str(conversation_id),))
            cursor.execute("""SELECT id, package_id, revision_id FROM public.conversation_skill_bindings
                WHERE conversation_id = %s AND skill_key = %s""", (conversation_id, candidate.skill_key))
            existing = cursor.fetchone()
            cursor.execute("""SELECT r.id FROM public.skill_revisions r
                JOIN public.skill_packages p ON p.id = r.package_id
                LEFT JOIN LATERAL (SELECT * FROM public.skill_assignments
                    WHERE package_id=p.id AND (org_id=%s::uuid OR org_id IS NULL)
                    ORDER BY org_id NULLS LAST LIMIT 1) a ON true
                WHERE r.package_id = %s AND r.revision = %s AND r.status = 'published'
                  AND ((p.scope_kind = 'personal' AND p.owner_user_id = %s::uuid)
                       OR (p.scope_kind = 'org' AND p.org_id = %s::uuid AND a.enabled AND a.revision_id=r.id)
                       OR (p.scope_kind = 'platform' AND a.enabled AND a.revision_id=r.id))""",
                (self.scope.org_id, candidate.package_id, candidate.revision,
                 self.scope.actor_user_id, self.scope.org_id))
            revision = cursor.fetchone()
            if not revision:
                raise SkillError("SKILL_BINDING_REVISION_UNAVAILABLE")
            if existing:
                if existing["package_id"] != candidate.package_id or existing["revision_id"] != revision["id"]:
                    raise SkillError("SKILL_BINDING_CONFLICT")
                return existing["id"]
            cursor.execute("""INSERT INTO public.conversation_skill_bindings
                (conversation_id, org_id, package_id, revision_id, skill_key, created_by)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                (conversation_id, self.scope.org_id, candidate.package_id, revision["id"],
                 candidate.skill_key, self.scope.actor_user_id))
            return cursor.fetchone()["id"]

    def remove_binding(self, conversation_id: UUID, binding_id: UUID) -> None:
        self._require_admin()
        with self._cursor() as cursor:
            cursor.execute("""DELETE FROM public.conversation_skill_bindings
                WHERE id = %s AND conversation_id = %s AND org_id IS NOT DISTINCT FROM %s::uuid""",
                (binding_id, conversation_id, self.scope.org_id))
