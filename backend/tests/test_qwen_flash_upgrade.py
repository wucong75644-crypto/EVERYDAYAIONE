"""Legacy model IDs and env overrides route to Flash."""
import pytest
from core.config import Settings
from services.adapters.factory import get_model_config

@pytest.mark.parametrize("old", ["qwen3.8-max", "qwen3.8-max-0902", "qwen3.5-plus"])
def test_legacy_max_routes_to_flash(old):
    settings = Settings(_env_file=None, database_url="test", jwt_secret_key="test", agent_loop_model=old, image_enhance_model=old)
    assert settings.agent_loop_model == "qwen3.8-flash"
    assert settings.image_enhance_model == "qwen3.8-flash"
    config = get_model_config(old)
    assert config.model_id == config.provider_model == "qwen3.8-flash"
    assert config.credits_per_1k_input == 0.012
