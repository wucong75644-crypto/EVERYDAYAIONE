"""Real disposable PostgreSQL: disabled Skills, tenant RLS and durable feedback."""

from uuid import uuid4

import psycopg
import pytest

from core.db_scope import DatabaseAccessKind, DatabaseScope, SET_DATABASE_SCOPE_SQL
from services.skills.recommendation_repository import SkillRecommendationRepository
from services.skills.recommendations import RecommendationFacts, recommend
from services.skills.resolver import SkillResolutionContext
from services.skills.contracts import SkillError
from tests.test_skill_bindings_postgres import configured, environment, postgres_socket, MIGRATIONS, action  # noqa: F401


@pytest.fixture
def rec_env(configured):
    env = configured
    with env.pool.connection(privileged=True) as conn:
        conn.execute((MIGRATIONS / "265_skill_recommendations.sql").read_text())
    env.facts = RecommendationFacts(context=SkillResolutionContext(
        actor_user_id=env.actor, org_id=env.org, conversation_scope="user", agent_domain="general",
        execution_mode="interactive", enabled_feature_flags={"skill_catalog_enabled", "skill_recommendations_enabled"}),
        available_tool_names={"file_search"})
    env.recommendations = recommend(env.facts, env.repo().catalog_candidates(), audience="user")
    env.audit = SkillRecommendationRepository(env.pool, DatabaseScope(str(env.actor), str(env.org), DatabaseAccessKind.PROJECTION))
    return env


def record(env, audience="user"):
    return env.audit.record(env.conversation, None, audience, env.facts, env.recommendations)


def test_durable_evidence_and_idempotent_feedback_are_append_only(rec_env):
    env = rec_env
    rid = record(env)
    selected = env.recommendations[0]
    assert env.audit.feedback(rid, env.conversation, selected.skill_id, selected.revision, "selected")
    assert env.audit.feedback(rid, env.conversation, selected.skill_id, selected.revision, "selected")
    assert env.audit.feedback(rid, env.conversation, selected.skill_id, selected.revision, "not_relevant")
    with env.audit._cursor() as cursor:
        cursor.execute("SELECT facts,candidates,algorithm_version FROM skill_recommendation_audits WHERE id=%s", (rid,))
        row = cursor.fetchone()
        assert row["algorithm_version"] == "trusted-facts-v1"
        assert set(row["candidates"][0]) == {"skill_id", "revision", "reasons"}
        assert row["facts"]["available_tool_names"] == ["file_search"]
        cursor.execute("SELECT feedback FROM skill_recommendation_feedback")
        assert {r["feedback"] for r in cursor.fetchall()} == {"selected", "not_relevant"}
    for table in ("skill_recommendation_audits", "skill_recommendation_feedback"):
        with env.pool.connection() as conn:
            conn.execute(SET_DATABASE_SCOPE_SQL, env.audit.scope.settings)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with conn.transaction():
                    conn.execute(f"DELETE FROM {table}")


def test_rls_blocks_foreign_org_user_conversation_and_forged_feedback(rec_env):
    env = rec_env
    rid = record(env)
    s = env.recommendations[0]
    for actor, org in ((env.member, env.org), (env.actor, env.other)):
        repo = SkillRecommendationRepository(env.pool, DatabaseScope(str(actor), str(org), DatabaseAccessKind.PROJECTION))
        with repo._cursor() as cursor:
            cursor.execute("SELECT * FROM skill_recommendation_audits")
            assert cursor.fetchall() == []
        assert not repo.feedback(rid, env.conversation, s.skill_id, s.revision, "selected")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            repo.record(env.conversation, None, "user", env.facts, env.recommendations)
    assert not env.audit.feedback(rid, env.other_conversation, s.skill_id, s.revision, "selected")
    assert not env.audit.feedback(rid, env.conversation, "forged", s.revision, "selected")
    assert not env.audit.feedback(rid, env.conversation, s.skill_id, "unknown", "selected")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with env.audit._cursor() as cursor:
            cursor.execute("""INSERT INTO skill_recommendation_feedback(recommendation_id,skill_id,revision,feedback)
                VALUES (%s,'forged','v1','selected')""", (rid,))


@pytest.mark.parametrize("kind", ["assignment", "revision"])
def test_disabled_skill_never_recommended_and_old_recommendation_cannot_activate(rec_env, kind):
    env = rec_env
    record(env)
    c = env.candidate
    if kind == "assignment":
        revision = env.repo().assigned_revision(c.package_id, c.revision)
        env.repo().set_assignment(c.package_id, revision.id, enabled=False)
    else:
        action(env.service(), env.pid, "disable")
    assert recommend(env.facts, env.repo().catalog_candidates(), audience="user") == []
    with pytest.raises(SkillError, match="UNAVAILABLE"):
        env.repo().assigned_revision(c.package_id, c.revision)


def test_feedback_audience_is_not_client_convertible(rec_env):
    env = rec_env
    rid = record(env, "model")
    c = env.recommendations[0]
    assert not env.audit.feedback(rid, env.conversation, c.skill_id, c.revision, "selected")
    assert env.audit.feedback(rid, env.conversation, c.skill_id, c.revision, "activated", audience="model")
    other = record(env)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        env.audit.feedback(other, env.conversation, c.skill_id, c.revision, "activated")


def test_membership_revocation_applies_to_audit_and_feedback(rec_env):
    env = rec_env
    rid = record(env)
    c = env.recommendations[0]
    with env.pool.connection(privileged=True) as conn:
        conn.execute("UPDATE org_members SET status='disabled' WHERE user_id=%s", (env.actor,))
    assert not env.audit.feedback(rid, env.conversation, c.skill_id, c.revision, "selected")


def test_empty_is_audited_and_rollback_preserves_evidence(rec_env):
    env = rec_env
    rid = env.audit.record(env.conversation, None, "user", env.facts, [])
    with env.audit._cursor() as cursor:
        cursor.execute("SELECT candidates FROM skill_recommendation_audits WHERE id=%s", (rid,))
        assert cursor.fetchone()["candidates"] == []
    rollback = (MIGRATIONS / "rollback/265_skill_recommendations_rollback.sql").read_text()
    with env.pool.connection(privileged=True) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="AUDITS_NOT_EMPTY"):
            conn.execute(rollback)


def test_empty_rollback_can_reapply(rec_env):
    with rec_env.pool.connection(privileged=True) as conn:
        conn.execute((MIGRATIONS / "rollback/265_skill_recommendations_rollback.sql").read_text())
        conn.execute((MIGRATIONS / "265_skill_recommendations.sql").read_text())



def test_file_type_preferences_require_reviewed_publication(rec_env):
    from services.skills.authoring_contracts import SaveDraft
    from tests.test_skill_authoring_postgres import publish
    env = rec_env
    svc = env.service()
    action(svc, env.pid, "start_draft")
    draft = svc.detail(env.pid)["draft"]
    content = {**draft["content"], "catalog_metadata": {
        **draft["content"]["catalog_metadata"], "recommended_file_types": ["pdf"]}}
    svc.save(env.pid, SaveDraft(expected_version=draft["version"], content=content))
    assert env.repo().catalog_candidates()[0].catalog_metadata.recommended_file_types == ()
    publish(svc, env.pid)
    c = env.repo().catalog_candidates()[0]
    assert c.catalog_metadata.recommended_file_types == ("pdf",)
    selected = env.facts.model_copy(update={"selected_file_types": frozenset({"pdf"})})
    assert any(reason.code == "file_type" for reason in recommend(selected, [c], audience="user")[0].reasons)
