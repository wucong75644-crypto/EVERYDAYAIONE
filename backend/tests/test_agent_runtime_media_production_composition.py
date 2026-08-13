from services.agent.runtime.production_composition import (
    ProductionSpecialistPorts, build_production_specialist_registry,
)


def _ports(**flags):
    return ProductionSpecialistPorts(
        transport=object(), erp_dispatcher=object(), erp_search=object(),
        artifact=object(), media_task=object(), resource_mutation=object(),
        child_run=object(), local_data=object(), file_analyze=object(),
        fetch_all_pages=object(), **flags,
    )


def test_production_media_provider_is_not_ready_by_default():
    registry = build_production_specialist_registry(_ports(), facts=object())
    _, executor = registry.resolve("generate_image")
    assert executor.provider.production_ready is False


def test_production_media_provider_requires_all_readiness_facts():
    registry = build_production_specialist_registry(
        _ports(media_provider_ready=True, media_credentials_ready=True,
               media_capability_enabled=True, kie_transport=object(),
               kie_credentials=object()),
        facts=object(),
    )
    _, executor = registry.resolve("generate_image")
    assert executor.provider.production_ready is True
