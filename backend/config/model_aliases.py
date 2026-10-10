"""Read-time model upgrades; persisted conversation records remain unchanged."""

MODEL_ALIASES = {
    "qwen3.5-plus": "qwen3.8-max",
    "qwen3.5-flash": "qwen3.8-flash",
}


def canonical_model_id(model_id: str) -> str:
    return MODEL_ALIASES.get(model_id, model_id)
