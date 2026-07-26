"""Real PostgreSQL regression contract for AR-06 acceptance corrections."""

from __future__ import annotations

import json
import os
import subprocess
import unittest

from backend.tests import (
    test_agent_runtime_core_foundation_postgres_external as foundation,
)


@unittest.skipUnless(
    os.getenv("RUN_AR06_DB_TEST") == "1" and foundation.DATABASE_URL,
    "RUN_AR06_DB_TEST=1 and AR06_TEST_DATABASE_URL are required",
)
class AgentRuntimeCoreCorrectionContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if "ar06" not in foundation.DATABASE_URL.lower():
            raise unittest.SkipTest("dedicated AR06 database name required")
        foundation._psql(foundation.BOOTSTRAP)
        for migration in foundation.MIGRATIONS:
            foundation._file(migration)

    def _assert_idempotency_contract(self) -> tuple[str, dict[str, object]]:
        session = json.loads(foundation._runtime(
            f"""
            SELECT ensure_agent_runtime_session(
                '{foundation.PERSONAL_CONVERSATION_ID}', NULL,
                '{foundation.USER_ID}', 'user', '{foundation.USER_ID}',
                '{foundation.USER_ID}', 'default', 'v1'
            );
            """,
            user_id=foundation.USER_ID,
        ))
        session_id = session["entity_id"]
        command = json.loads(foundation._runtime(
            f"""
            SELECT submit_session_command(
                '{session_id}', 'submit_input', '  spaced-command  ',
                '{{"text":"hi"}}'
            );
            """,
            user_id=foundation.USER_ID,
        ))
        repeated_command = json.loads(foundation._runtime(
            f"""
            SELECT submit_session_command(
                '{session_id}', 'submit_input', 'spaced-command',
                '{{"text":"hi"}}'
            );
            """,
            user_id=foundation.USER_ID,
        ))
        self.assertEqual(repeated_command["outcome"], "already_exists")
        self.assertEqual(repeated_command["entity_id"], command["entity_id"])
        self.assertEqual(
            foundation._value(
                "SET ROLE everydayai_owner;"
                "SELECT idempotency_key FROM agent_session_commands"
                f" WHERE id = '{command['entity_id']}';"
            ),
            "spaced-command",
        )

        run = json.loads(foundation._worker(
            f"""
            SELECT create_agent_run(
                '{session_id}', '{command["entity_id"]}', '  spaced-run  ',
                'user', '{{"context":1}}', '{{"config":1}}',
                '{{"capability":1}}'
            );
            """
        ).stdout.strip().splitlines()[-1])
        repeated_run = json.loads(foundation._worker(
            f"""
            SELECT create_agent_run(
                '{session_id}', '{command["entity_id"]}', 'spaced-run',
                'user', '{{"context":1}}', '{{"config":1}}',
                '{{"capability":1}}'
            );
            """
        ).stdout.strip().splitlines()[-1])
        self.assertEqual(repeated_run["outcome"], "already_exists")
        self.assertEqual(repeated_run["entity_id"], run["entity_id"])

        different_key = json.loads(foundation._worker(
            f"""
            SELECT create_agent_run(
                '{session_id}', '{command["entity_id"]}', 'different-key',
                'user', '{{"context":1}}', '{{"config":1}}',
                '{{"capability":1}}'
            );
            """
        ).stdout.strip().splitlines()[-1])
        self.assertEqual(different_key["outcome"], "already_exists")
        self.assertEqual(different_key["entity_id"], run["entity_id"])
        different_snapshot = json.loads(foundation._worker(
            f"""
            SELECT create_agent_run(
                '{session_id}', '{command["entity_id"]}', 'spaced-run',
                'user', '{{"context":1}}', '{{"config":2}}',
                '{{"capability":1}}'
            );
            """
        ).stdout.strip().splitlines()[-1])
        self.assertEqual(
            different_snapshot["outcome"],
            "idempotency_conflict",
        )
        return session_id, run

    def _assert_concurrent_command_run(self, session_id: str) -> None:
        concurrent_command = json.loads(foundation._runtime(
            f"""
            SELECT submit_session_command(
                '{session_id}', 'submit_input', 'concurrent-command', '{{}}'
            );
            """,
            user_id=foundation.USER_ID,
        ))
        concurrent_sql = lambda key: f"""
            SET SESSION AUTHORIZATION everydayai_worker;
            SELECT set_config('app.actor_user_id', '', false);
            SELECT set_config('app.org_id', '', false);
            SELECT set_config('app.access_kind', 'worker', false);
            SELECT set_config('app.request_id', 'concurrent-create', false);
            SELECT create_agent_run(
                '{session_id}', '{concurrent_command["entity_id"]}', '{key}',
                'user', '{{}}', '{{}}', '{{}}'
            );
        """
        processes = [
            subprocess.Popen(
                [
                    "psql", "--no-psqlrc", "--set=ON_ERROR_STOP=1",
                    "--tuples-only", "--no-align",
                    "--dbname", foundation.DATABASE_URL,
                    "--command", concurrent_sql(key),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for key in ("concurrent-a", "concurrent-b")
        ]
        outcomes = [
            json.loads(process.communicate()[0].strip().splitlines()[-1])
            for process in processes
        ]
        self.assertEqual(
            sorted(item["outcome"] for item in outcomes),
            ["already_exists", "created"],
        )
        self.assertEqual(outcomes[0]["entity_id"], outcomes[1]["entity_id"])
        self.assertEqual(
            foundation._value(
                "SET ROLE everydayai_owner;"
                "SELECT count(*) FROM agent_runs"
                f" WHERE command_id = '{concurrent_command['entity_id']}';"
            ),
            "1",
        )

    def _assert_model_step_fencing(self, run: dict[str, object]) -> None:
        claim = json.loads(foundation._worker(
            f"SELECT claim_agent_run('{run['entity_id']}', 'worker', 90, 3);"
        ).stdout.strip().splitlines()[-1])
        step = json.loads(foundation._worker(
            f"""
            SELECT create_model_step(
                '{run["entity_id"]}', '{claim["execution_token"]}',
                'model', 'provider', 'model-v1', 'prompt-v1', 'tools-v1',
                '{{}}'
            );
            """
        ).stdout.strip().splitlines()[-1])
        foundation._psql(
            "SET ROLE everydayai_owner;"
            "UPDATE agent_runs SET lease_expires_at = clock_timestamp()"
            f" - INTERVAL '1 second' WHERE id = '{run['entity_id']}';"
        )
        expired_step = json.loads(foundation._worker(
            f"""
            SELECT fail_model_step(
                '{step["entity_id"]}', '{claim["execution_token"]}',
                0, 'provider_timeout'
            );
            """
        ).stdout.strip().splitlines()[-1])
        self.assertEqual(expired_step["outcome"], "lease_expired")
        reclaimed = json.loads(foundation._worker(
            f"SELECT claim_agent_run('{run['entity_id']}', 'worker-2', 90, 3);"
        ).stdout.strip().splitlines()[-1])
        failed_step = json.loads(foundation._worker(
            f"""
            SELECT fail_model_step(
                '{step["entity_id"]}', '{reclaimed["execution_token"]}',
                0, 'provider_timeout'
            );
            """
        ).stdout.strip().splitlines()[-1])
        self.assertEqual(failed_step["outcome"], "failed")
        conflicting_failure = json.loads(foundation._worker(
            f"""
            SELECT fail_model_step(
                '{step["entity_id"]}', '{reclaimed["execution_token"]}',
                1, 'different_error'
            );
            """
        ).stdout.strip().splitlines()[-1])
        self.assertEqual(conflicting_failure["outcome"], "terminal_conflict")

    def _assert_projection_fencing(self) -> None:
        outboxes = json.loads(foundation._worker(
            "SELECT claim_agent_projection_outbox(100, 60);"
        ).stdout.strip().splitlines()[-1])
        outbox = outboxes[0]
        foundation._psql(
            "SET ROLE everydayai_owner;"
            "UPDATE agent_projection_outbox SET lease_expires_at ="
            " clock_timestamp() - INTERVAL '1 second'"
            f" WHERE id = '{outbox['id']}';"
        )
        expired_projection = json.loads(foundation._worker(
            f"""
            SELECT fail_agent_projection_outbox(
                '{outbox["id"]}', '{outbox["lease_token"]}', 'transport'
            );
            """
        ).stdout.strip().splitlines()[-1])
        self.assertEqual(expired_projection["outcome"], "lease_expired")
        reclaimed_outboxes = json.loads(foundation._worker(
            "SELECT claim_agent_projection_outbox(100, 60);"
        ).stdout.strip().splitlines()[-1])
        reclaimed_outbox = next(
            item for item in reclaimed_outboxes if item["id"] == outbox["id"]
        )
        completed_projection = json.loads(foundation._worker(
            f"""
            SELECT complete_agent_projection_outbox(
                '{outbox["id"]}', '{reclaimed_outbox["lease_token"]}',
                '{{"sequence":1}}'
            );
            """
        ).stdout.strip().splitlines()[-1])
        self.assertEqual(completed_projection["outcome"], "completed")
        checkpoint_conflict = json.loads(foundation._worker(
            f"""
            SELECT complete_agent_projection_outbox(
                '{outbox["id"]}', '{reclaimed_outbox["lease_token"]}',
                '{{"sequence":2}}'
            );
            """
        ).stdout.strip().splitlines()[-1])
        self.assertEqual(checkpoint_conflict["outcome"], "terminal_conflict")

    def _assert_schema_contract(self) -> None:
        schema_contract = foundation._value("""
            SELECT
                (SELECT count(*) FROM information_schema.columns
                  WHERE table_name = 'agent_runtime_events'
                    AND column_name = ANY(ARRAY[
                        'scope_kind', 'scope_id', 'durability', 'action_id',
                        'causation_event_id', 'redaction_revision',
                        'trace_id', 'span_id'
                    ])) = 8
                AND EXISTS (
                    SELECT 1 FROM pg_constraint
                     WHERE conrelid = 'agent_session_commands'::regclass
                       AND pg_get_constraintdef(oid) LIKE '%submit_input%'
                       AND pg_get_constraintdef(oid) LIKE '%switch_agent%'
                )
                AND EXISTS (
                    SELECT 1 FROM pg_constraint
                     WHERE conrelid = 'agent_model_steps'::regclass
                       AND pg_get_constraintdef(oid) LIKE '%pending%'
                )
                AND EXISTS (
                    SELECT 1 FROM pg_constraint
                     WHERE conrelid = 'agent_run_attempts'::regclass
                       AND pg_get_constraintdef(oid) LIKE '%crashed%'
                )
                AND EXISTS (
                    SELECT 1 FROM pg_constraint
                     WHERE conrelid = 'agent_runtime_events'::regclass
                       AND pg_get_constraintdef(oid) LIKE '%reconciler%'
                );
        """)
        self.assertEqual(schema_contract, "t")

    def test_idempotency_fencing_and_ar05_schema_contract(self) -> None:
        session_id, run = self._assert_idempotency_contract()
        self._assert_concurrent_command_run(session_id)
        self._assert_model_step_fencing(run)
        self._assert_projection_fencing()
        self._assert_schema_contract()
