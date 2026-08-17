from __future__ import annotations

import pytest

from services.agent.runtime.providers import legacy_kie_adapter


class _Client:
    def __init__(self, api_key: str):
        self.api_key = api_key


class _ImageAdapter:
    def __init__(self, client, model):
        self.client = client
        self.model = model


class _VideoAdapter:
    def __init__(self, client, model):
        self.client = client
        self.model = model


@pytest.mark.parametrize(
    ("kind", "adapter", "model"),
    (("image", _ImageAdapter, "gpt-image-2-image-to-image"),
     ("video", _VideoAdapter, "sora-2-text-to-video")),
)
def test_runtime_legacy_factory_uses_runtime_key_and_legacy_adapter(
    monkeypatch, kind, adapter, model,
):
    monkeypatch.setattr(legacy_kie_adapter, "KieClient", _Client)
    monkeypatch.setattr(legacy_kie_adapter, "KieImageAdapter", _ImageAdapter)
    monkeypatch.setattr(legacy_kie_adapter, "KieVideoAdapter", _VideoAdapter)

    result = legacy_kie_adapter.RuntimeLegacyKieAdapterFactory().create(
        kind, model, "runtime-key",
    )

    assert isinstance(result, adapter)
    assert result.client.api_key == "runtime-key"
    assert result.model == model


def test_runtime_legacy_factory_requires_runtime_key():
    with pytest.raises(ValueError, match="API_KEY_REQUIRED"):
        legacy_kie_adapter.RuntimeLegacyKieAdapterFactory().create(
            "image", "gpt-image-2-text-to-image", None,
        )
