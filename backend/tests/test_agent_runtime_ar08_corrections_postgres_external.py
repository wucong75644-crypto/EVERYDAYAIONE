"""Real PostgreSQL contracts for the AR-08 audit corrections."""

from __future__ import annotations

import json
import os
import subprocess
import unittest

from backend.tests import (
    test_agent_runtime_core_foundation_postgres_external as foundation,
)


SYSTEM_SESSION = "88888888-8888-8888-8888-888888888881"
SYSTEM_CONVERSATION = "88888888-8888-8888-8888-888888888882"
MIDDLE_GAP_SESSION = "88888888-8888-8888-8888-888888888883"
MIDDLE_GAP_CONVERSATION = "88888888-8888-8888-8888-888888888884"
TAIL_GAP_SESSION = "88888888-8888-8888-8888-888888888885"
TAIL_GAP_CONVERSATION = "88888888-8888-8888-8888-888888888886"


def _scoped(
    role: str,
    sql: str,
    *,
    user_id: str = "",
    org_id: str = "",
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    prefix = f"""
        SET SESSION AUTHORIZATION {role};
        SELECT set_config('app.actor_user_id', '{user_id}', false);
        SELECT set_config('app.org_id', '{org_id}', false);
        SELECT set_config(
            'app.access_kind',
            '{"worker" if role == "everydayai_worker" else "runtime"}',
            false
        );
        SELECT set_config('app.request_id', 'ar08-correction', false);
    """
    return foundation._psql(prefix + sql, check=check)


def _json(
    result: subprocess.CompletedProcess[str] | str,
) -> dict[str, object]:
    output = result if isinstance(result, str) else result.stdout
    return json.loads(output.strip().splitlines()[-1])


def _create_system_session(
    session_id: str, conversation_id: str, event_count: int,
) -> None:
    foundation._psql(f"""
        SET ROLE everydayai_owner;
        INSERT INTO conversations(id, user_id, org_id, scope_type, scope_id)
        VALUES ('{conversation_id}', NULL, NULL, 'system', 'system:test');
        INSERT INTO agent_runtime_sessions(
            id, conversation_id, org_id, user_id, scope_kind, scope_id,
            created_by_user_id, agent_definition_id,
            agent_definition_revision
        ) VALUES (
            '{session_id}', '{conversation_id}', NULL, NULL,
            'system', 'system:test', NULL, 'system', 'v1'
        );
    """)
    for sequence in range(1, event_count + 1):
        foundation._psql(f"""
            SET ROLE everydayai_owner;
            SELECT append_agent_runtime_event(
                '{session_id}', 'system.event', NULL, NULL,
                gen_random_uuid(), 'system', NULL,
                '{{"sequence":{sequence}}}', ARRAY[]::TEXT[]
            );
        """)


def _create_run(
    session_id: str,
    command_id: str,
    idempotency_key: str,
) -> dict[str, object]:
    return _json(foundation._worker(f"""
        SELECT create_agent_run(
            '{session_id}', '{command_id}', '{idempotency_key}',
            'user', '{{}}', '{{}}', '{{}}'
        );
    """))


@unittest.skipUnless(
    os.getenv("RUN_AR06_DB_TEST") == "1" and foundation.DATABASE_URL,
    "RUN_AR06_DB_TEST=1 and AR06_TEST_DATABASE_URL are required",
)
class AgentRuntimeAr08CorrectionContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if "ar06" not in foundation.DATABASE_URL.lower():
            raise unittest.SkipTest("dedicated AR06 database name required")
        foundation._psql(foundation.BOOTSTRAP)
        for migration in foundation.MIGRATIONS:
            foundation._file(migration)

    def _create_scoped_sessions(self) -> tuple[dict, dict, dict]:
        personal = _json(foundation._runtime(
            f"""
            SELECT ensure_agent_runtime_session(
                '{foundation.PERSONAL_CONVERSATION_ID}', NULL,
                '{foundation.USER_ID}', 'user', '{foundation.USER_ID}',
                '{foundation.USER_ID}', 'default', 'v1'
            );
            """,
            user_id=foundation.USER_ID,
        ))
        organization = _json(foundation._runtime(
            f"""
            SELECT ensure_agent_runtime_session(
                '{foundation.ORG_CONVERSATION_ID}', '{foundation.ORG_ID}',
                '{foundation.ORG_USER_ID}', 'user',
                '{foundation.ORG_USER_ID}', '{foundation.ORG_USER_ID}',
                'default', 'v1'
            );
            """,
            user_id=foundation.ORG_USER_ID,
            org_id=foundation.ORG_ID,
        ))
        channel = _json(foundation._runtime(
            f"""
            SELECT ensure_agent_runtime_session(
                '{foundation.CHANNEL_CONVERSATION_ID}',
                '{foundation.ORG_ID}', NULL, 'channel',
                'wecom:group:test', '{foundation.ORG_USER_ID}',
                'default', 'v1'
            );
            """,
            user_id=foundation.ORG_USER_ID,
            org_id=foundation.ORG_ID,
        ))
        return personal, organization, channel

    def _assert_read_scope_matrix(
        self, personal: dict, organization: dict, channel: dict,
    ) -> None:
        own = _json(foundation._runtime(
            f"SELECT get_agent_runtime_session('{personal['entity_id']}');",
            user_id=foundation.USER_ID,
        ))
        self.assertEqual(own["outcome"], "found")
        valid_channel = _json(_scoped(
            "everydayai_wecom_runtime",
            f"SELECT get_agent_runtime_session('{channel['entity_id']}');",
            user_id=foundation.SECOND_MEMBER_ID,
            org_id=foundation.ORG_ID,
        ))
        self.assertEqual(valid_channel["outcome"], "found")
        denied_reads = (
            _scoped(
                "everydayai_runtime",
                f"SELECT get_agent_runtime_session('{organization['entity_id']}');",
                user_id=foundation.USER_ID,
                check=False,
            ),
            _scoped(
                "everydayai_runtime",
                f"SELECT get_agent_runtime_session('{organization['entity_id']}');",
                user_id=foundation.ORG_USER_ID,
                check=False,
            ),
            _scoped(
                "everydayai_wecom_runtime",
                f"SELECT get_agent_runtime_session('{channel['entity_id']}');",
                user_id=foundation.USER_ID,
                org_id=foundation.ORG_ID,
                check=False,
            ),
            _scoped(
                "everydayai_wecom_runtime",
                f"SELECT get_agent_runtime_session('{channel['entity_id']}');",
                user_id=foundation.SECOND_MEMBER_ID,
                check=False,
            ),
        )
        for denied in denied_reads:
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn("AGENT_RUNTIME_READ_SCOPE_MISMATCH", denied.stderr)
        for role in ("everydayai_runtime", "everydayai_wecom_runtime"):
            for sql in (
                f"SELECT get_agent_runtime_session('{SYSTEM_SESSION}');",
                f"SELECT replay_agent_runtime_events("
                f"'{SYSTEM_SESSION}', 0, 100);",
            ):
                denied = _scoped(
                    role, sql, user_id=foundation.USER_ID, check=False,
                )
                self.assertNotEqual(denied.returncode, 0)
                self.assertIn(
                    "AGENT_RUNTIME_READ_SCOPE_MISMATCH", denied.stderr,
                )
        worker_session = _json(foundation._worker(
            f"SELECT get_agent_runtime_session('{SYSTEM_SESSION}');"
        ))
        worker_events = _json(foundation._worker(
            f"SELECT replay_agent_runtime_events('{SYSTEM_SESSION}', 0, 100);"
        ))
        self.assertEqual(worker_session["outcome"], "found")
        self.assertEqual(len(worker_events["events"]), 5)

    def _assert_cancel_matrix(
        self, personal: dict, organization: dict, channel: dict,
    ) -> None:
        personal_command = _json(foundation._runtime(
            f"""
            SELECT submit_session_command(
                '{personal["entity_id"]}', 'cancel', 'cancel-personal', '{{}}'
            );
            """,
            user_id=foundation.USER_ID,
        ))
        personal_run = _create_run(
            personal["entity_id"], personal_command["entity_id"],
            "run-cancel-personal",
        )
        unauthorized = _scoped(
            "everydayai_runtime",
            f"SELECT cancel_agent_run('{personal_run['entity_id']}', 0, 'x');",
            user_id=foundation.ORG_USER_ID,
            check=False,
        )
        self.assertIn("AGENT_RUNTIME_CANCEL_SCOPE_MISMATCH", unauthorized.stderr)
        runtime_cancel = _json(foundation._runtime(
            f"""
            SELECT cancel_agent_run(
                '{personal_run["entity_id"]}', 0, 'user_cancelled'
            );
            """,
            user_id=foundation.USER_ID,
        ))
        self.assertEqual(runtime_cancel["outcome"], "cancelled")

        channel_command = _json(_scoped(
            "everydayai_wecom_runtime",
            f"""
            SELECT submit_session_command(
                '{channel["entity_id"]}', 'cancel', 'cancel-channel', '{{}}'
            );
            """,
            user_id=foundation.SECOND_MEMBER_ID,
            org_id=foundation.ORG_ID,
        ))
        channel_run = _create_run(
            channel["entity_id"], channel_command["entity_id"],
            "run-cancel-channel",
        )
        nonmember = _scoped(
            "everydayai_wecom_runtime",
            f"SELECT cancel_agent_run('{channel_run['entity_id']}', 0, 'x');",
            user_id=foundation.USER_ID,
            org_id=foundation.ORG_ID,
            check=False,
        )
        self.assertIn("AGENT_RUNTIME_CANCEL_SCOPE_MISMATCH", nonmember.stderr)
        cross_enterprise = _scoped(
            "everydayai_wecom_runtime",
            f"SELECT cancel_agent_run('{channel_run['entity_id']}', 0, 'x');",
            user_id=foundation.SECOND_MEMBER_ID,
            check=False,
        )
        self.assertIn(
            "AGENT_RUNTIME_CANCEL_SCOPE_MISMATCH", cross_enterprise.stderr,
        )
        wecom_cancel = _json(_scoped(
            "everydayai_wecom_runtime",
            f"""
            SELECT cancel_agent_run(
                '{channel_run["entity_id"]}', 0, 'channel_cancelled'
            );
            """,
            user_id=foundation.SECOND_MEMBER_ID,
            org_id=foundation.ORG_ID,
        ))
        self.assertEqual(wecom_cancel["outcome"], "cancelled")

        worker_command = _json(foundation._runtime(
            f"""
            SELECT submit_session_command(
                '{organization["entity_id"]}', 'cancel',
                'cancel-worker', '{{}}'
            );
            """,
            user_id=foundation.ORG_USER_ID,
            org_id=foundation.ORG_ID,
        ))
        worker_run = _create_run(
            organization["entity_id"], worker_command["entity_id"],
            "run-cancel-worker",
        )
        worker_cancel = _json(foundation._worker(f"""
            SELECT cancel_agent_run(
                '{worker_run["entity_id"]}', 0, 'worker_cancelled'
            );
        """))
        self.assertEqual(worker_cancel["outcome"], "cancelled")

    def _assert_replay_checkpoints(self) -> None:
        full = _json(foundation._worker(
            f"SELECT replay_agent_runtime_events('{SYSTEM_SESSION}', 0, 100);"
        ))
        tail = _json(foundation._worker(
            f"SELECT replay_agent_runtime_events('{SYSTEM_SESSION}', 5, 100);"
        ))
        self.assertEqual(
            [event["sequence"] for event in full["events"]],
            [1, 2, 3, 4, 5],
        )
        self.assertEqual(tail["events"], [])
        ahead = foundation._worker(
            f"SELECT replay_agent_runtime_events('{SYSTEM_SESSION}', 6, 100);",
            check=False,
        )
        self.assertIn("AGENT_RUNTIME_REPLAY_CHECKPOINT_AHEAD", ahead.stderr)
        pages = [
            _json(foundation._worker(
                f"SELECT replay_agent_runtime_events("
                f"'{SYSTEM_SESSION}', {after}, 2);"
            ))
            for after in (0, 2, 4)
        ]
        self.assertEqual(
            [
                event["sequence"]
                for page in pages
                for event in page["events"]
            ],
            [1, 2, 3, 4, 5],
        )
        foundation._psql(
            "SET ROLE everydayai_owner;"
            f"DELETE FROM agent_runtime_events"
            f" WHERE session_id = '{MIDDLE_GAP_SESSION}' AND sequence = 3;"
            f"DELETE FROM agent_runtime_events"
            f" WHERE session_id = '{TAIL_GAP_SESSION}' AND sequence = 5;"
        )
        for session_id in (MIDDLE_GAP_SESSION, TAIL_GAP_SESSION):
            gap = foundation._worker(
                f"SELECT replay_agent_runtime_events('{session_id}', 0, 100);",
                check=False,
            )
            self.assertIn("AGENT_RUNTIME_EVENT_SEQUENCE_GAP", gap.stderr)
        missing = _json(foundation._worker(
            "SELECT replay_agent_runtime_events("
            "'99999999-9999-9999-9999-999999999999', 0, 100);"
        ))
        self.assertEqual(missing["outcome"], "not_found")

    def _assert_privileges_and_rollback(self) -> None:
        matrix = foundation._value("""
            SELECT
                NOT has_function_privilege(
                    'public', 'get_agent_runtime_session(uuid)', 'EXECUTE')
                AND has_function_privilege(
                    'everydayai_runtime',
                    'cancel_agent_run(uuid,bigint,text)', 'EXECUTE')
                AND has_function_privilege(
                    'everydayai_wecom_runtime',
                    'cancel_agent_run(uuid,bigint,text)', 'EXECUTE')
                AND NOT has_function_privilege(
                    'everydayai_runtime',
                    'complete_agent_run(uuid,uuid,bigint,text)', 'EXECUTE')
                AND NOT has_table_privilege(
                    'everydayai_runtime', 'agent_runtime_events', 'SELECT')
                AND NOT has_table_privilege(
                    'everydayai_worker', 'agent_runtime_events', 'SELECT')
                AND (
                    SELECT count(*) = 7 FROM pg_class
                     WHERE relname = ANY(ARRAY[
                        'agent_runtime_sessions', 'agent_session_commands',
                        'agent_runs', 'agent_run_attempts',
                        'agent_model_steps', 'agent_runtime_events',
                        'agent_projection_outbox'
                     ]) AND relrowsecurity AND relforcerowsecurity
                );
        """)
        self.assertEqual(matrix, "t")
        foundation._file(foundation.ROLLBACKS[0])
        after_rollback = foundation._value("""
            SELECT
                to_regprocedure('get_agent_runtime_session(uuid)') IS NULL
                AND NOT has_table_privilege(
                    'everydayai_runtime', 'agent_runtime_events', 'SELECT')
                AND (
                    SELECT count(*) = 7 FROM pg_class
                     WHERE relname LIKE 'agent_%'
                       AND relrowsecurity AND relforcerowsecurity
                );
        """)
        self.assertEqual(after_rollback, "t")
        foundation._file(foundation.MIGRATIONS[-1])
        self.assertEqual(
            foundation._value("""
                SELECT has_function_privilege(
                    'everydayai_worker',
                    'replay_agent_runtime_events(uuid,bigint,integer)',
                    'EXECUTE'
                );
            """),
            "t",
        )

    def test_scope_cancel_replay_and_permissions(self) -> None:
        personal, organization, channel = self._create_scoped_sessions()
        for session_id, conversation_id in (
            (SYSTEM_SESSION, SYSTEM_CONVERSATION),
            (MIDDLE_GAP_SESSION, MIDDLE_GAP_CONVERSATION),
            (TAIL_GAP_SESSION, TAIL_GAP_CONVERSATION),
        ):
            _create_system_session(session_id, conversation_id, 5)
        self._assert_read_scope_matrix(personal, organization, channel)
        self._assert_cancel_matrix(personal, organization, channel)
        self._assert_replay_checkpoints()
        self._assert_privileges_and_rollback()
