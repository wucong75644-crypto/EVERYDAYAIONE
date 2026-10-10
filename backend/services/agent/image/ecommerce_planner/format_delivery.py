"""Bounded, auditable formatting; creative text and reference order stay untouched."""
from __future__ import annotations

from copy import deepcopy
import json
import re

from .contracts import VISUAL_SECTIONS, text_hash, validate_schema
from .delivery import _unique_object, _invalid_constant

PROMPTS_VERSION = 'ecom-prompts.v4'
FORMAT_VERSION = 'ecom-format.v1'
PROMPTS_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['prompts'],
    'properties': {'prompts': {'type': 'array', 'items': {'type': 'string', 'minLength': 1}}}}


class FormatError(ValueError):
    def __init__(self, code, paths=()):
        self.code = code
        self.paths = [list(path) for path in paths]
        super().__init__(code + (': ' + json.dumps(self.paths, ensure_ascii=False) if self.paths else ''))


def strict_object(text):
    body = text.strip()
    fenced = re.fullmatch(r'```(?:json)?\s*\n?(.*?)\n?```', body, re.S)
    if fenced:
        body = fenced[1].strip()
    value = json.loads(body, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    if not isinstance(value, dict):
        raise FormatError('PLANNER_JSON_OBJECT_REQUIRED')
    return value


def normalize_object(value, schema, *, root=None, path=(), audit=None):
    """Only known keys at schema locations, or unknown nulls; no global renaming."""
    root = root or schema
    audit = audit if audit is not None else []
    if '$ref' in schema:
        schema = root['$defs'][schema['$ref'].rsplit('/', 1)[1]]
    schema = _container_branch(value, schema, root)
    if isinstance(value, dict):
        props = schema.get('properties', {})
        result = {}
        for key, child in value.items():
            canonical = key[1:] if key.startswith('/') and key[1:] in props else key
            if canonical in result or canonical != key and canonical in value:
                raise FormatError('PLANNER_NORMALIZATION_COLLISION', [(*path, key)])
            if canonical != key:
                audit.append({'path': [*path, key], 'operation': 'known_key', 'key': canonical})
            if canonical not in props and schema.get('additionalProperties') is False and child is None:
                audit.append({'path': [*path, key], 'operation': 'null_extra'})
                continue
            result[canonical] = normalize_object(child, props.get(canonical, {}), root=root,
                path=(*path, canonical), audit=audit)
        return result
    if isinstance(value, list):
        return [normalize_object(child, schema.get('items', {}), root=root, path=(*path, i), audit=audit)
                for i, child in enumerate(value)]
    return value


def _container_branch(value, schema, root):
    for branch in schema.get('anyOf', []):
        if '$ref' in branch:
            branch = root['$defs'][branch['$ref'].rsplit('/', 1)[1]]
        if (isinstance(value, dict) and branch.get('type') == 'object'
                or isinstance(value, list) and branch.get('type') == 'array'):
            return branch
    return schema


def schema_paths(value, schema, *, root=None, path=()):
    """Locate protocol defects using the same schema vocabulary as the legacy validator."""
    root = root or schema
    if '$ref' in schema:
        return schema_paths(value, root['$defs'][schema['$ref'].rsplit('/', 1)[1]], root=root, path=path)
    try:
        validate_schema(value, schema, root)
        return []
    except ValueError:
        pass
    if 'anyOf' in schema:
        branch = _container_branch(value, schema, root)
        if branch is not schema:
            return schema_paths(value, branch, root=root, path=path)
    paths = []
    for branch in schema.get('allOf', []):
        paths += schema_paths(value, branch, root=root, path=path)
    if 'if' in schema:
        try:
            validate_schema(value, schema['if'], root)
            branch = schema.get('then', {})
        except ValueError:
            branch = schema.get('else', {})
        paths += schema_paths(value, branch, root=root, path=path)
    if isinstance(value, dict):
        props = schema.get('properties', {})
        paths += [[*path, key] for key in schema.get('required', []) if key not in value]
        if schema.get('additionalProperties') is False:
            paths += [[*path, key] for key in value if key not in props]
        for key, child in value.items():
            if key in props:
                paths += schema_paths(child, props[key], root=root, path=(*path, key))
    if isinstance(value, list):
        for i, child in enumerate(value):
            paths += schema_paths(child, schema.get('items', {}), root=root, path=(*path, i))
        if 'contains' in schema:
            matches = 0
            for child in value:
                try:
                    validate_schema(child, schema['contains'], root)
                    matches += 1
                except ValueError:
                    pass
            if not schema.get('minContains', 1) <= matches <= schema.get('maxContains', float('inf')):
                for i, child in enumerate(value):
                    if isinstance(child, dict):
                        paths += [[*path, i, key] for key in schema['contains'].get('properties', {})]
    return paths or [list(path)]


def decode_formatted(text, schema):
    raw = strict_object(text) if isinstance(text, str) else deepcopy(text)
    if not isinstance(raw, dict):
        raise FormatError('PLANNER_JSON_OBJECT_REQUIRED')
    audit = []
    value = normalize_object(raw, schema, audit=audit)
    return value, {'format_version': FORMAT_VERSION, 'normalization': audit,
        'response_sha256': text_hash(text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)),
        'canonical_sha256': text_hash(json.dumps(value, ensure_ascii=False))}


def _visual_parts(text):
    """Identify known Markdown headings, never a quoted marker or an inline substring."""
    if not isinstance(text, str):
        raise FormatError('PLANNER_VISUAL_SECTIONS_INVALID')
    fenced = re.fullmatch(r'\s*```(?:markdown|md)?\s*\n(.*?)\n```\s*', text, re.S)
    if fenced:
        text = fenced[1]
    matches = []
    in_fence = False
    offset = 0
    for line in text.splitlines(keepends=True):
        if re.match(r'^\s*```', line):
            in_fence = not in_fence
        if not in_fence:
            heading = re.fullmatch(r'\s*#{1,6}\s+(.+?)\s*#*\s*', line.rstrip('\r\n'))
            if heading:
                title = re.sub(r'^\d+\s*[.、．)）:]\s*', '', heading[1].strip().strip('*').strip())
                title = title.strip().strip('*').strip()
                if title in VISUAL_SECTIONS:
                    matches.append((title, offset, offset + len(line)))
        offset += len(line)
    found = [title for title, _, _ in matches]
    bodies = [text[start: matches[i + 1][1] if i + 1 < len(matches) else len(text)].strip()
              for i, (_, _, start) in enumerate(matches)]
    prefix = text[:matches[0][1]].strip() if matches else ''
    return prefix, [bodies[found.index(title)] if found.count(title) == 1 else None for title in VISUAL_SECTIONS]


def partial_visual_sections(text):
    return _visual_parts(text)[1]


def visual_sections(text):
    bodies = partial_visual_sections(text)
    bad = [i for i, body in enumerate(bodies) if not body or not re.sub(r'[#*\s-]', '', body)]
    if bad:
        raise FormatError('PLANNER_VISUAL_SECTIONS_INVALID', [('sections', i) for i in bad])
    return bodies


def render_visual(sections, prefix=''):
    body = '\n\n'.join(f'## {i}. {title}\n{body}' for i, (title, body) in enumerate(zip(VISUAL_SECTIONS, sections), 1))
    return prefix + '\n\n' + body if prefix else body


def normalize_visual(text):
    sections = visual_sections(text)
    return render_visual(sections, _visual_parts(text)[0])


def validate_questions(value):
    if set(value) != {'questions'} or not isinstance(value['questions'], list) or not 1 <= len(value['questions']) <= 3:
        raise FormatError('PLANNER_QUESTIONS_INVALID')
    if any(not isinstance(q, str) or not q.strip() for q in value['questions']):
        raise FormatError('PLANNER_QUESTIONS_INVALID')
    return {'status': 'needs_input', 'questions': value['questions']}


def prompt_positions(value, snapshot):
    """Return invalid output positions without selecting/reordering input reference images."""
    if set(value) != {'prompts'} or not isinstance(value['prompts'], list):
        raise FormatError('PLANNER_PROMPTS_FIELDS_INVALID', [('prompts',)])
    prompts = value['prompts']; count = snapshot['image_count']; bad = []
    from .inputs import is_product
    products = {i for i, r in enumerate(snapshot['references'], 1) if is_product(r['role'])}
    seen = set()
    for i in range(count):
        body = prompts[i] if i < len(prompts) else None
        if not isinstance(body, str) or not body.strip() or body in seen:
            bad.append(i + 1); continue
        seen.add(body)
        aliases = re.findall(r'(?<![A-Za-z0-9_])image_(\d+)(?![A-Za-z0-9_])', body)
        numbers = {int(x) for x in aliases}
        if (not numbers & products or any(x.startswith('0') for x in aliases)
                or any(n < 1 or n > len(snapshot['references']) for n in numbers)):
            bad.append(i + 1)
    if len(prompts) > count:
        bad = list(range(1, count + 1))
    return bad


def preserve_leaves(previous, current, excluded=(), path=()):
    """An LLM saying 'unchanged' is not evidence; compare every protected leaf."""
    if any(tuple(path[:len(p)]) == tuple(p) for p in excluded):
        return
    if isinstance(previous, dict):
        if not isinstance(current, dict):
            raise FormatError('PLANNER_VALID_CONTENT_CHANGED', [path])
        for key, value in previous.items():
            if key not in current:
                if any(tuple((*path, key)[:len(p)]) == tuple(p) for p in excluded):
                    continue
                raise FormatError('PLANNER_VALID_CONTENT_CHANGED', [(*path, key)])
            preserve_leaves(value, current[key], excluded, (*path, key))
    elif isinstance(previous, list):
        if not isinstance(current, list) or len(current) < len(previous):
            raise FormatError('PLANNER_VALID_CONTENT_CHANGED', [path])
        for i, value in enumerate(previous):
            preserve_leaves(value, current[i], excluded, (*path, i))
    elif type(previous) is not type(current) or previous != current:
        raise FormatError('PLANNER_VALID_CONTENT_CHANGED', [path])


def preserve_string_tokens(raw, formatted):
    # For syntactically broken JSON no AST exists. Require every complete
    # quoted token (keys and values) in the same order; never guess/rewrite text.
    def tokens(text):
        pattern = r'"(?:[^"\\]|\\.)*"|(?<![\w])(?:-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null)(?![\w])'
        if re.sub(r'[\s{}\[\],:]', '', re.sub(pattern, '', text)):
            raise FormatError('PLANNER_FORMAT_CONTENT_UNPROVABLE')
        return [(m, json.loads(m[0])) for m in re.finditer(pattern, text)]
    try:
        before, after = tokens(raw), tokens(formatted)
    except ValueError as error:
        raise FormatError('PLANNER_FORMAT_CONTENT_UNPROVABLE') from error
    if not before or len(before) != len(after):
        raise FormatError('PLANNER_VALID_CONTENT_CHANGED')
    restored = formatted
    for (_, previous), (match, current) in reversed(list(zip(before, after))):
        if type(previous) is not type(current):
            raise FormatError('PLANNER_VALID_CONTENT_CHANGED')
        if previous != current:
            # Formatters may trim paragraph padding. Restore the exact original
            # string rather than accept changed values or spend another call.
            if not isinstance(previous, str) or previous.strip() != current.strip():
                raise FormatError('PLANNER_VALID_CONTENT_CHANGED')
            restored = restored[:match.start()] + json.dumps(previous, ensure_ascii=False) + restored[match.end():]
    return restored


def product_paths(value, schema, snapshot):
    paths = schema_paths(value, schema)
    if paths:
        return paths
    from .inputs import source_bindings, is_product
    bindings = source_bindings(snapshot)
    groups = ('facts', 'selling_points', 'questions', 'gaps')
    ids = {group: {item['id'] for item in value[group]} for group in groups}
    paths = []
    for group in groups:
        seen = set()
        for i, item in enumerate(value[group]):
            if item['id'] in seen:
                paths.append([group, i, 'id'])
            seen.add(item['id'])
    for i, fact in enumerate(value['facts']):
        for j, source in enumerate(fact['sources']):
            ref = bindings.get(source['source_ref'])
            if (not ref or (source['kind'] in {'image_observation', 'image_text'}) != (ref['kind'] == 'image')
                    or ref['kind'] == 'image' and not is_product(ref['role'])):
                paths.append(['facts', i, 'sources', j])
    if not set(value['product']['fact_ids']) <= ids['facts']:
        paths.append(['product', 'fact_ids'])
    usable = {f['id'] for f in value['facts'] if f['status'] == 'usable'}
    for group, field, allowed in [('selling_points', 'fact_ids', usable), ('questions', 'gap_ids', ids['gaps']),
            ('gaps', 'affected_selling_point_ids', ids['selling_points'])]:
        for i, item in enumerate(value[group]):
            if not set(item[field]) <= allowed:
                paths.append([group, i, field])
    return paths


def merge_values(previous, paths, values, schema):
    if not isinstance(values, list) or len(values) != len(paths):
        raise FormatError('PLANNER_REPAIR_TARGET_COUNT')
    result = deepcopy(previous)
    for path, value in zip(paths, values):
        if not path:
            raise FormatError('PLANNER_REPAIR_TARGET_UNLOCATABLE')
        parent, spec = result, schema
        for key in path[:-1]:
            if '$ref' in spec:
                spec = schema['$defs'][spec['$ref'].rsplit('/', 1)[1]]
            spec = spec.get('items', {}) if isinstance(key, int) else spec.get('properties', {}).get(key, {})
            parent = parent[key]
        if '$ref' in spec:
            spec = schema['$defs'][spec['$ref'].rsplit('/', 1)[1]]
        key = path[-1]
        extra = isinstance(parent, dict) and spec.get('additionalProperties') is False and key not in spec.get('properties', {})
        if extra:
            if value is not None:
                raise FormatError('PLANNER_REPAIR_EXTRA_REQUIRES_NULL', [path])
            parent.pop(key, None)
        else:
            parent[key] = value
    preserve_leaves(previous, result, paths)
    return result
