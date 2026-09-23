"""Bounded literal substitution. No filesystem, environment, eval or template engine."""

import hashlib
import json
import math
import re

from services.skills.contracts import SkillError
from services.skills.assets import MAX_ASSET_BYTES, public_assets, verify_asset

# UTF-8 byte caps are conservative token upper bounds, independent of provider.
MAX_BODY_BYTES = 16_384
MAX_ARGS_BYTES = 4_096
MAX_RENDERED_BYTES = 24_576
MAX_TURN_RENDERED_BYTES = 49_152
MAX_DIRECTORY_BYTES = 12_288
MAX_DIRECTORY_ENTRIES = 32
MAX_ACTIVE_SKILLS = 4
MAX_ARGUMENTS = 16
_VARIABLE = re.compile(r"\{\{args\.([a-z][a-z0-9_]{0,31})\}\}")
_ASSET_REFERENCE = re.compile(r'\[\[asset:([a-z][a-z0-9_-]{0,63})\]\]')


class SkillTemplateArgsError(SkillError):
    def __init__(self, required_args):
        super().__init__("SKILL_TEMPLATE_ARGS_MISMATCH")
        self.required_args = sorted(required_args)


def encoded(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def bounded(value: str, maximum: int, code: str) -> str:
    if len(value.encode("utf-8")) > maximum:
        raise SkillError(code)
    return value


def argument_summary(args: object) -> dict:
    if not isinstance(args, dict) or len(args) > MAX_ARGUMENTS:
        raise SkillError("SKILL_ARGS_INVALID")
    for key, value in args.items():
        if (not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", key)
                or type(value) not in (str, int, float, bool)
                or (type(value) is float and not math.isfinite(value))):
            raise SkillError("SKILL_ARGS_INVALID")
    try:
        serialized = encoded(args)
    except (ValueError, UnicodeError):
        raise SkillError("SKILL_ARGS_INVALID") from None
    bounded(serialized, MAX_ARGS_BYTES, "SKILL_ARGS_BUDGET_EXCEEDED")
    # Checkpoints retain a digest, names and byte count, not a second raw copy.
    return {"sha256": digest(serialized), "keys": sorted(args), "bytes": len(serialized.encode("utf-8"))}


def render(body: str, args: dict, *, maximum_body: int = MAX_BODY_BYTES) -> str:
    bounded(body, maximum_body, "SKILL_BODY_BUDGET_EXCEEDED")
    argument_summary(args)
    names = set(_VARIABLE.findall(body))
    remainder = _VARIABLE.sub("", body)
    if "{{" in remainder or "}}" in remainder or "${" in remainder or "{%" in remainder:
        raise SkillError("SKILL_TEMPLATE_VARIABLE_FORBIDDEN")
    if len(names) > MAX_ARGUMENTS:
        raise SkillError("SKILL_TEMPLATE_ARGUMENT_LIMIT")
    if names != set(args):
        raise SkillTemplateArgsError(names)
    # Only caller-provided scalar values; replacements are never reinterpreted.
    rendered = _VARIABLE.sub(
        lambda match: args[match[1]] if isinstance(args[match[1]], str) else encoded(args[match[1]]),
        body,
    )
    return bounded(rendered, MAX_RENDERED_BYTES, "SKILL_RENDER_BUDGET_EXCEEDED")


def referenced_assets(skill) -> tuple[str, ...]:
    ids = tuple(dict.fromkeys(_ASSET_REFERENCE.findall(skill.body)))
    if '[[asset:' in _ASSET_REFERENCE.sub('', skill.body):
        raise SkillError('SKILL_ASSET_REFERENCE_INVALID')
    if not set(ids) <= {a.id for a in skill.resources.assets}:
        raise SkillError('SKILL_ASSET_NOT_DECLARED')
    return ids


def _template_names(text: str, declarations) -> set[str]:
    names = set(_VARIABLE.findall(text))
    remainder = _VARIABLE.sub('', text)
    if any(marker in remainder for marker in ('{{', '}}', '${', '{%')):
        raise SkillError('SKILL_TEMPLATE_VARIABLE_FORBIDDEN')
    if not names <= declarations.keys():
        raise SkillError('SKILL_TEMPLATE_VARIABLE_UNDECLARED')
    return names


def validate_resource_templates(skill, texts):
    referenced_assets(skill)
    _template_names(skill.body, skill.resources.template_variables)
    for asset in skill.resources.assets:
        if asset.kind == 'template':
            _template_names(texts[asset.id], skill.resources.template_variables)


def server_arguments(resources, context: dict) -> dict:
    values = {}
    for name, declaration in resources.template_variables.items():
        value = context.get(declaration.source)
        expected = str if declaration.type == 'string' else bool
        if type(value) is not expected:
            raise SkillError('SKILL_TEMPLATE_SERVER_VALUE_UNAVAILABLE')
        values[name] = value
    argument_summary(values)
    return values


def asset_manifest_digest(resources) -> str | None:
    # Omitted sources preserve the digest of revisions/checkpoints published
    # before file uploads were supported.
    return digest(encoded(resources.model_dump(mode='json', exclude_none=True))) if resources.assets or resources.template_variables else None


def prepare_resources(skill, context: dict, maximum: int):
    """Determine reads from the original body, before substitution or asset IO."""
    ids = referenced_assets(skill)
    values = server_arguments(skill.resources, context)
    names = _template_names(skill.body, skill.resources.template_variables)
    base = render(skill.body, {name: values[name] for name in names})
    if skill.resources.assets:
        base += '\n\n[Skill attachments: summaries]\n' + encoded(public_assets(skill.resources))
    entries = {a.id: a for a in skill.resources.assets}
    required = len(base.encode('utf-8')) + sum(
        len(asset_header(entries[asset_id]).encode('utf-8')) + entries[asset_id].bytes for asset_id in ids)
    if required > maximum:
        raise SkillError('SKILL_ASSET_BUDGET_EXCEEDED' if skill.resources.assets else 'SKILL_TURN_BUDGET_EXCEEDED')
    return ids, base, values


def asset_header(asset):
    return (f'\n\n[Skill attachment: {asset.id}]\n'
            + encoded({'name': asset.name, 'kind': asset.kind}) + '\n')


def render_resources(skill, ids, base, values, texts, maximum):
    if set(texts) != set(ids):
        raise SkillError('SKILL_ASSET_NOT_DECLARED')
    entries = {a.id: a for a in skill.resources.assets}
    for asset_id in ids:
        entry = entries[asset_id]
        content = verify_asset(entry, texts[asset_id].encode('utf-8'))
        if entry.kind == 'template':
            names = _template_names(content, skill.resources.template_variables)
            content = render(content, {name: values[name] for name in names}, maximum_body=MAX_ASSET_BYTES)
        base += asset_header(entry) + content
        bounded(base, maximum, 'SKILL_ASSET_BUDGET_EXCEEDED')
    return base
