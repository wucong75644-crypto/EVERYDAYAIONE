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
                    p.scope_kind, b.org_id AS assignment_org_id,
                    COALESCE(a.priority, 0) AS priority, r.revision,
                    r.summary AS description, r.catalog_metadata,
                    (COALESCE(a.enabled, FALSE) AND r.status = 'published') AS available
                FROM public.conversation_skill_bindings b
                JOIN public.skill_packages p ON p.id = b.package_id
                JOIN public.skill_revisions r ON r.id = b.revision_id AND r.package_id = b.package_id
                LEFT JOIN public.skill_assignments a ON a.package_id = b.package_id AND a.org_id = b.org_id
                WHERE b.conversation_id = %s AND b.org_id = %s
                ORDER BY b.created_at, b.id""", (conversation_id, self._require_org()))
            return cursor.fetchall()

    @staticmethod
    def candidate(row: dict) -> SkillCandidate:
        return SkillCandidate.model_validate({key: row[key] for key in SkillCandidate.model_fields})

    def add_binding(self, conversation_id: UUID, candidate: SkillCandidate) -> UUID:
        self._require_admin()
        org_id = self._require_org()
        with self._cursor() as cursor:
            self.lock_package_write(cursor, candidate.package_id)
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                           ('skill-binding:' + str(conversation_id),))
            cursor.execute("""SELECT id, package_id, revision_id FROM public.conversation_skill_bindings
                WHERE conversation_id = %s AND skill_key = %s""", (conversation_id, candidate.skill_key))
            existing = cursor.fetchone()
            cursor.execute("""SELECT r.id FROM public.skill_revisions r
                JOIN public.skill_assignments a ON a.package_id = r.package_id AND a.revision_id = r.id
                WHERE r.package_id = %s AND r.revision = %s AND r.status = 'published'
                    AND a.org_id = %s AND a.enabled""", (candidate.package_id, candidate.revision, org_id))
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
                (conversation_id, org_id, candidate.package_id, revision["id"],
                 candidate.skill_key, self.scope.actor_user_id))
            return cursor.fetchone()["id"]

    def remove_binding(self, conversation_id: UUID, binding_id: UUID) -> None:
        self._require_admin()
        with self._cursor() as cursor:
            cursor.execute("""DELETE FROM public.conversation_skill_bindings
                WHERE id = %s AND conversation_id = %s AND org_id = %s""",
                (binding_id, conversation_id, self._require_org()))
