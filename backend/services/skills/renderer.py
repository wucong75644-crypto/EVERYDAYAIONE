"""Bounded literal substitution. No filesystem, environment, eval or template engine."""

import hashlib
import json
import math
import re

from services.skills.contracts import SkillError

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


def render(body: str, args: dict) -> str:
    bounded(body, MAX_BODY_BYTES, "SKILL_BODY_BUDGET_EXCEEDED")
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
