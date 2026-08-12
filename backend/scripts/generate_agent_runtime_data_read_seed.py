"""Generate the immutable Runtime data-read release seed."""

from __future__ import annotations

import json
from pathlib import Path

from services.agent.runtime.catalog.data_read_release import (
    build_data_read_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "migrations/227_58_agent_runtime_data_read_release.sql"


def main(target: Path = TARGET) -> None:
    snapshots = [
        build_data_read_snapshot(scope=scope, channel=channel, gate_state=gate)
        for scope in ("user", "channel")
        for channel in ("web", "wecom")
        for gate in ("enabled", "disabled")
    ]
    first = snapshots[0]
    definition = first.definition
    definition_document = {
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
    catalog_revision = str(first.catalog_document["catalog_revision"])
    channel_default = next(
        item for item in snapshots
        if item.toolset_document["scope_kind"] == "channel"
        and item.toolset_document["channel"] == "web"
        and item.toolset_document["gate_state"] == "disabled"
    )
    rows = []
    for item in snapshots:
        document = item.toolset_document
        rows.append("      (" + ", ".join([
            sql_text(definition.canonical_key), sql_text(definition.revision),
            sql_text(catalog_revision), sql_text(str(document["scope_kind"])),
            sql_text(str(document["channel"])), sql_text(str(document["gate_state"])),
            sql_text(item.toolset_hash), sql_json(document) + "::JSONB",
            "FALSE", "TRUE",
        ]) + ")")
    target.write_text(f"""-- S2 generated Runtime data-read release. Do not edit facts by hand.
-- Source: services/agent/runtime/catalog/data_read_release.py
SET LOCAL ROLE everydayai_owner;

INSERT INTO agent_runtime_catalog_facts(
  catalog_revision,catalog_hash,catalog_document,enabled_for_new_ingress,recoverable
) VALUES(
  {sql_text(catalog_revision)},{sql_text(catalog_revision)},
  {sql_json(first.catalog_document)}::JSONB,FALSE,TRUE
);

INSERT INTO agent_runtime_definition_facts(
  agent_key,definition_revision,definition_hash,prompt_revision,catalog_revision,
  effective_toolset_hash,definition_document,enabled_for_new_ingress,recoverable
) VALUES(
  {sql_text(definition.canonical_key)},{sql_text(definition.revision)},
  {sql_text(definition.definition_hash)},{sql_text(definition.prompt_revision)},
  {sql_text(catalog_revision)},{sql_text(channel_default.toolset_hash)},
  {sql_json(definition_document)}::JSONB,FALSE,TRUE
);

INSERT INTO agent_runtime_effective_toolset_facts(
  agent_key,definition_revision,catalog_revision,scope_kind,channel,gate_state,
  effective_toolset_hash,toolset_document,enabled_for_new_ingress,recoverable
) VALUES
{',\n'.join(rows)};

RESET ROLE;
""", encoding="utf-8")


def sql_text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_json(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return "$seed$" + encoded.replace("$seed$", "\\u0024seed\\u0024") + "$seed$"


if __name__ == "__main__":
    main()
