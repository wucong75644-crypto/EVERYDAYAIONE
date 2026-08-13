"""Registry merge with one shared specialist-facts owner."""

from services.agent.runtime.executors.registry import ExecutorRegistry


def merge_runtime_registries(*registries: ExecutorRegistry) -> ExecutorRegistry:
    result = ExecutorRegistry()
    facts = {
        id(registry.specialist_facts): registry.specialist_facts
        for registry in registries if registry.specialist_facts is not None
    }
    if len(facts) > 1:
        raise RuntimeError("RUNTIME_SPECIALIST_FACTS_CONFLICT")
    result.specialist_facts = next(iter(facts.values()), None)
    for registry in registries:
        for descriptor in registry.descriptors():
            action_kind = next(iter(descriptor.action_kinds))
            _, executor = registry.resolve(action_kind)
            result.register(
                descriptor, executor,
                safety_level=registry.safety_level(action_kind),
            )
    return result


__all__ = ["merge_runtime_registries"]
