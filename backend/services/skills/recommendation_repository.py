"""Append-only recommendation evidence and explicit feedback; no Skill mutation."""

from uuid import uuid4

from psycopg.types.json import Jsonb

from services.skills.recommendations import ALGORITHM_VERSION
from services.skills.repository import SkillRepository


class SkillRecommendationRepository(SkillRepository):
    def record(self, conversation_id, turn_id, audience, facts, candidates):
        recommendation_id = uuid4()
        # Persist typed facts only. No names, descriptions, prompts, file paths,
        # uploaded text, model text, or arbitrary feedback strings.
        evidence = {
            "domain": facts.context.agent_domain,
            "execution_mode": facts.context.execution_mode,
            "available_tool_names": sorted(facts.available_tool_names),
            "selected_file_types": sorted(facts.selected_file_types),
            "session_bindings": facts.session_bindings,
        }
        items = [{"skill_id": c.skill_id, "revision": c.revision,
                  "reasons": [r.model_dump(mode="json") for r in c.reasons]}
                 for c in candidates]
        with self._cursor() as cursor:
            cursor.execute("""INSERT INTO public.skill_recommendation_audits
                (id,org_id,actor_user_id,conversation_id,turn_id,audience,algorithm_version,facts,candidates)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (
                recommendation_id, self._require_org(), self.scope.actor_user_id,
                conversation_id, turn_id, audience, ALGORITHM_VERSION, Jsonb(evidence), Jsonb(items)))
        return recommendation_id

    def feedback(self, recommendation_id, conversation_id, skill_id, revision, feedback, *, audience="user"):
        with self._cursor() as cursor:
            cursor.execute("""SELECT id FROM public.skill_recommendation_audits
                WHERE id=%s AND conversation_id=%s AND org_id=%s AND actor_user_id=%s AND audience=%s
                AND candidates @> %s::jsonb""", (
                recommendation_id, conversation_id, self._require_org(), self.scope.actor_user_id,
                audience, Jsonb([{"skill_id": skill_id, "revision": revision}])))
            if cursor.fetchone() is None:
                return False
            cursor.execute("""INSERT INTO public.skill_recommendation_feedback
                (recommendation_id,skill_id,revision,feedback) VALUES (%s,%s,%s,%s)
                ON CONFLICT (recommendation_id,skill_id,revision,feedback) DO NOTHING""",
                (recommendation_id, skill_id, revision, feedback))
        return True
