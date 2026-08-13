"""Regenerate immutable Runtime image-pricing facts from KIE configuration."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from config.kie_models import KIE_MODEL_CONFIGS, KieModelCategory


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "migrations/228_04_agent_runtime_media_action_bindings.sql"
SMART_MODELS = ROOT / "config/smart_models.json"
START = "-- BEGIN GENERATED RUNTIME MEDIA PRICING FACTS"
END = "-- END GENERATED RUNTIME MEDIA PRICING FACTS"
PRICING_REVISION = "kie-image-pricing-v1"


def build_pricing_rows() -> tuple[dict[str, object], ...]:
    """Return deterministic per-image prices used by the database contract."""
    smart = json.loads(SMART_MODELS.read_text(encoding="utf-8"))
    default_model = smart["image"]["default"]
    rows: list[dict[str, object]] = []
    for model_id, config in sorted(KIE_MODEL_CONFIGS.items()):
        if config.get("category") != KieModelCategory.IMAGE:
            continue
        resolutions = config.get("user_credits_per_image_by_resolution")
        if isinstance(resolutions, dict):
            prices = sorted((str(key), int(value)) for key, value in resolutions.items())
        else:
            prices = [("default", int(config["user_credits_per_image"]))]
        for resolution_key, user_credits in prices:
            fact = {
                "pricing_revision": PRICING_REVISION,
                "model_id": model_id,
                "resolution_key": resolution_key,
                "user_credits": user_credits,
                "active": bool(config.get("is_active", False)),
                "supports_resolution": bool(config.get("supports_resolution", False)),
                "requires_image_input": bool(config.get("requires_image_input", False)),
                "is_default_model": model_id == default_model,
            }
            fact["fact_hash"] = hashlib.sha256(
                json.dumps(
                    fact, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
            ).hexdigest()
            rows.append(fact)
    default_rows = tuple(row for row in rows if row["is_default_model"])
    if not default_rows or {
        str(row["model_id"]) for row in default_rows
    } != {default_model}:
        raise RuntimeError("RUNTIME_MEDIA_DEFAULT_PRICING_FACTS_INVALID")
    return tuple(rows)


def render_pricing_seed() -> str:
    values = []
    for row in build_pricing_rows():
        values.append("    (" + ", ".join((
            _sql_text(str(row["pricing_revision"])),
            _sql_text(str(row["model_id"])),
            _sql_text(str(row["resolution_key"])),
            str(row["user_credits"]),
            _sql_bool(bool(row["active"])),
            _sql_bool(bool(row["supports_resolution"])),
            _sql_bool(bool(row["requires_image_input"])),
            _sql_bool(bool(row["is_default_model"])),
            _sql_text(str(row["fact_hash"])),
        )) + ")")
    return (
        f"{START}\n"
        "INSERT INTO agent_runtime_media_pricing_facts(\n"
        "    pricing_revision, model_id, resolution_key, user_credits, active,\n"
        "    supports_resolution, requires_image_input, is_default_model, fact_hash\n"
        ") VALUES\n" + ",\n".join(values) + ";\n" + END
    )


def main(target: Path = TARGET) -> None:
    source = target.read_text(encoding="utf-8")
    start = source.index(START)
    end = source.index(END, start) + len(END)
    target.write_text(
        source[:start] + render_pricing_seed() + source[end:],
        encoding="utf-8",
    )


def _sql_text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_bool(value: bool) -> str:
    return "TRUE" if value else "FALSE"


if __name__ == "__main__":
    main()
