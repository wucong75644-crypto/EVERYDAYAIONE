"""Generate the immutable Runtime v12 batch-media release seed."""

from __future__ import annotations

import json
from pathlib import Path

from services.agent.runtime.catalog.batch_media_release import (
    build_batch_media_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "migrations/230_05_agent_runtime_catalog_batch_media_v12.sql"


def main(target: Path = TARGET) -> None:
    snapshots = [
        build_batch_media_snapshot(scope=scope, channel=channel, gate_state=gate)
        for scope in ("user", "channel")
        for channel in ("web", "wecom")
        for gate in ("enabled", "disabled")
    ]
    first = snapshots[0]
    definition = build_batch_media_definition_document()
    catalog = first.receipt.document()
    catalog_revision = str(catalog["catalog_revision"])
    default = next(
        snapshot for snapshot in snapshots
        if snapshot.scope_kind == "user"
        and snapshot.channel == "web"
        and snapshot.gate_state == "disabled"
    )
    rows = [_toolset_row(snapshot, catalog_revision) for snapshot in snapshots]
    target.write_text(
        _migration_sql(
            catalog=catalog,
            catalog_revision=catalog_revision,
            definition=definition,
            effective_toolset_hash=default.toolset.toolset_hash,
            rows=rows,
        ),
        encoding="utf-8",
    )


def build_batch_media_definition_document() -> dict[str, object]:
    from services.agent.runtime.catalog.batch_media_release import (
        build_batch_media_definition,
    )

    definition = build_batch_media_definition()
    return {
        "canonical_key": definition.canonical_key,
        "revision": definition.revision,
        "prompt_revision": definition.prompt_revision,
        "requested_tool_groups": sorted(definition.requested_tool_groups),
        "model_policy": dict(definition.model_policy),
        "context_policy": dict(definition.context_policy),
        "channel_restrictions": sorted(definition.channel_restrictions),
        "system_prompt": definition.system_prompt,
        "definition_hash": definition.definition_hash,
    }


def _toolset_row(snapshot: object, catalog_revision: str) -> str:
    document = snapshot.document()["toolset"]
    document["entitled_groups"] = sorted(
        build_batch_media_definition_document()["requested_tool_groups"],
    )
    return "      (" + ", ".join([
        sql_text("everydayai-default"),
        sql_text(str(build_batch_media_definition_document()["revision"])),
        sql_text(catalog_revision), sql_text(str(document["scope_kind"])),
        sql_text(str(document["channel"])), sql_text(str(document["gate_state"])),
        sql_text(str(document["toolset_hash"])), sql_json(document) + "::JSONB",
        "FALSE", "TRUE",
    ]) + ")"


def _migration_sql(
    *, catalog: dict[str, object], catalog_revision: str,
    definition: dict[str, object], effective_toolset_hash: str,
    rows: list[str],
) -> str:
    return f"""-- Runtime v12 batch-media release. Do not edit facts by hand.
-- Source: services/agent/runtime/catalog/batch_media_release.py
SET LOCAL ROLE everydayai_owner;

DO $$
DECLARE
  catalog_doc JSONB := {sql_json(catalog)}::JSONB;
  catalog_rev TEXT := {sql_text(catalog_revision)};
BEGIN
  INSERT INTO agent_runtime_catalog_facts(
    catalog_revision,catalog_hash,catalog_document,enabled_for_new_ingress,recoverable
  ) VALUES(catalog_rev,catalog_rev,catalog_doc,FALSE,TRUE)
  ON CONFLICT (catalog_revision) DO NOTHING;

  INSERT INTO agent_runtime_definition_facts(
    agent_key,definition_revision,definition_hash,prompt_revision,catalog_revision,
    effective_toolset_hash,definition_document,enabled_for_new_ingress,recoverable
  ) VALUES(
    'everydayai-default',{sql_text(str(definition['revision']))},
    {sql_text(str(definition['definition_hash']))},
    {sql_text(str(definition['prompt_revision']))},catalog_rev,
    {sql_text(effective_toolset_hash)},{sql_json(definition)}::JSONB,FALSE,TRUE
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
  FROM jsonb_array_elements(catalog_doc->'tools') tool
  ON CONFLICT (catalog_revision,tool_name) DO NOTHING;
END $$;

RESET ROLE;
"""


def sql_text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_json(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return "$seed$" + encoded.replace("$seed$", "\\u0024seed\\u0024") + "$seed$"


if __name__ == "__main__":
    main()
