"""Executable protocols and exact, reviewed legacy contracts. No name-based grants."""
import json
from functools import lru_cache
from pathlib import Path

from .contracts import CoordinationContract, SkillError

METHODS = {
    ('image.planning', 'ecommerce.inline'): frozenset({'plan'}),
    ('image.planning', 'ecommerce.server_plan'): frozenset({'plan'}),
    ('image.submission', 'image.direct'): frozenset({'execute_prompt', 'edit'}),
    ('image.submission', 'image.plan_source'): frozenset({'execute_ready'}),
    ('image.prompting', 'image.prompt'): frozenset({'plan'}),
}


def validate_protocols(contract):
    if contract:
        for claim in contract.claims:
            allowed = METHODS.get((claim.scope, claim.method))
            if allowed is None or not set(claim.actions) <= allowed:
                raise SkillError('SKILL_WORKFLOW_METHOD_UNAVAILABLE')
    return contract


@lru_cache(maxsize=1)
def legacy_contracts():
    path = Path(__file__).parents[2] / 'config/skill_workflow_compatibility.json'
    rows = json.loads(path.read_text())['entries']
    return {(r['package_id'], r['revision'], r['body_sha256']):
            (r['skill_key'], validate_protocols(CoordinationContract.model_validate(r['contract'])))
            for r in rows}


def contract_for(candidate, body_sha256):
    if candidate.catalog_metadata.coordination is not None:
        return validate_protocols(candidate.catalog_metadata.coordination)
    found = legacy_contracts().get((str(candidate.package_id), candidate.revision, body_sha256))
    return found[1] if found and found[0] == candidate.skill_key else None


def image_planning_participants(participants):
    """Prompt writing and image planning produce the same per-image brief."""
    return {p.skill_key for p in participants if p.contract and not p.phase_resident
            and any(c.scope in {'image.prompting', 'image.planning'} and 'plan' in c.actions
                    for c in p.contract.claims)}


def required_protocol_tools(composition):
    from .coordination import method_for
    result = set()
    if method_for(composition, 'image.submission'):
        result.add('generate_image')
    if method_for(composition, 'image.planning') == 'ecommerce.server_plan':
        result.add('plan_ecommerce_images')
    return result


def incompatible_protocol_tools(composition):
    """Protocol availability is narrower than authority; never grants permissions."""
    from .coordination import method_for
    # The server planner requires ecommerce.server_plan. An inline owner must
    # not be told to activate a competing owner to use this tool.
    if (method_for(composition, 'image.planning') == 'ecommerce.inline'
            or method_for(composition, 'image.prompting') == 'image.prompt'):
        return frozenset({'plan_ecommerce_images'})
    return frozenset()
