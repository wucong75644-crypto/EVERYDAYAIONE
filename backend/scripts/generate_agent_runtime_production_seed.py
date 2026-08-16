"""Generate the frozen AR-17.4 catalog seed from runtime descriptors."""

from __future__ import annotations

import json
from pathlib import Path

from services.agent.runtime.catalog.production_seed import (
    build_seed_agent, build_seed_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "migrations/230_01_agent_runtime_catalog_production_v8.sql"


def main() -> None:
    agent = build_seed_agent()
    catalog = build_seed_snapshot().receipt.document()
    catalog_revision = str(catalog["catalog_revision"])
    definition = {
        "canonical_key": agent.canonical_key,
        "revision": agent.revision,
        "prompt_revision": agent.prompt_revision,
        "requested_tool_groups": sorted(agent.requested_tool_groups),
        "model_policy": dict(agent.model_policy),
        "context_policy": dict(agent.context_policy),
        "channel_restrictions": sorted(agent.channel_restrictions),
        "system_prompt": agent.system_prompt,
        "definition_hash": agent.definition_hash,
    }
    rows: list[str] = []
    for scope in ("user", "channel"):
        for channel in ("web", "wecom"):
            for gate in ("enabled", "disabled"):
                snapshot = build_seed_snapshot(
                    scope=scope, channel=channel, gate_state=gate,
                )
                document = snapshot.document()["toolset"]
                document["entitled_groups"] = sorted(agent.requested_tool_groups)
                rows.append(
                    "      (" + ", ".join([
                        sql_text("everydayai-default"), sql_text(agent.revision),
                        sql_text(catalog_revision), sql_text(scope),
                        sql_text(channel), sql_text(gate),
                        sql_text(str(document["toolset_hash"])),
                        sql_json(document) + "::JSONB", "FALSE", "TRUE",
                    ]) + ")"
                )
    toolset_hash = build_seed_snapshot(
        scope="user", channel="web", gate_state="enabled",
    ).toolset.toolset_hash
    catalog_json = sql_json(catalog)
    definition_json = sql_json(definition)
    sql = f"""-- AR-17.4 generated seed. Do not edit facts by hand.
-- Source: services/agent/runtime/catalog/production_seed.py
SET LOCAL ROLE everydayai_owner;

DO $$
DECLARE
  catalog_doc JSONB := {catalog_json}::JSONB;
  catalog_rev TEXT := {sql_text(catalog_revision)};
  definition_doc JSONB := {definition_json}::JSONB;
  definition_hash TEXT := {sql_text(agent.definition_hash)};
BEGIN
  INSERT INTO agent_runtime_catalog_facts(
    catalog_revision,catalog_hash,catalog_document,enabled_for_new_ingress,recoverable
  ) VALUES(catalog_rev,catalog_rev,catalog_doc,FALSE,TRUE);

  INSERT INTO agent_runtime_definition_facts(
    agent_key,definition_revision,definition_hash,prompt_revision,catalog_revision,
    effective_toolset_hash,definition_document,enabled_for_new_ingress,recoverable
  ) VALUES(
    'everydayai-default',{sql_text(agent.revision)},definition_hash,
    {sql_text(agent.prompt_revision)},catalog_rev,
    {sql_text(toolset_hash)},definition_doc,FALSE,TRUE
  );

  INSERT INTO agent_runtime_effective_toolset_facts(
    agent_key,definition_revision,catalog_revision,scope_kind,channel,gate_state,
    effective_toolset_hash,toolset_document,enabled_for_new_ingress,recoverable
  ) VALUES
{',\n'.join(rows)};

  INSERT INTO agent_runtime_production_bindings(
    catalog_revision,tool_name,provider_revision,secret_binding,readiness_hash,ready
  )
  SELECT catalog_rev,tool->>'canonical_name',tool->>'provider_revision',
    CASE WHEN tool->>'safety_level'='safe' AND tool->>'side_effect'='none'
      THEN NULL ELSE 'secret-binding:'||(tool->>'canonical_name') END,
    encode(digest(('readiness:'||(tool->>'canonical_name')||':'||
      (tool->>'provider_revision'))::bytea,'sha256'),'hex'),FALSE
  FROM jsonb_array_elements(catalog_doc->'tools') tool;
END $$;

RESET ROLE;
"""
    TARGET.write_text(sql, encoding="utf-8")


def sql_text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_json(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "$seed$" + encoded.replace("$seed$", "\\u0024seed\\u0024") + "$seed$"


if __name__ == "__main__":
    main()
