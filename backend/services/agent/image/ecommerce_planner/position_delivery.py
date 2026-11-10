"""Versioned per-position prose delivery; completed work survives delivery windows."""
from copy import deepcopy
import asyncio
import json
import re

from .contracts import text_hash
from .format_delivery import (FORMAT_VERSION, FormatError, strict_object, normalize_visual,
    schema_paths, validate_questions, prompt_positions)
from .inputs import model_input, user_content

POSITION_VERSION = 'ecom-prompts.v5'
POSITION_PROFILE = 'jd.v2'
MAX_POSITION_CONCURRENCY = 6
OUTLINE_SCHEMA = {'type': 'object', 'additionalProperties': False,
    'required': ['visual_spec', 'shared_rules', 'outline'], 'properties': {
        'visual_spec': {'type': 'string', 'minLength': 1},
        'shared_rules': {'type': 'string', 'minLength': 1},
        'outline': {'type': 'array', 'items': {'type': 'string', 'minLength': 1}}}}


_OUTLINE_HEADING = re.compile(r'^第[ \t]*([1-9]\d*)[ \t]*[张屏][ \t]*[｜|：:]', re.M)


def split_numbered_outline(items, count):
    """Split only when every proposed boundary has an unambiguous ordered label."""
    if not isinstance(items, list) or not items or not all(isinstance(item, str) for item in items):
        return items
    blocks = []
    for item in items:
        headings = list(_OUTLINE_HEADING.finditer(item))
        if not headings or headings[0].start() != 0:
            return items
        blocks.extend((int(h.group(1)), item[h.start():end].removesuffix('\n'))
            for h, end in zip(headings, [h.start() for h in headings[1:]] + [len(item)]))
    if len(blocks) > len(items) and [n for n, _ in blocks] == list(range(1, min(count, len(blocks)) + 1)):
        return [body for _, body in blocks]
    return items


def outline_label_matches(body, position):
    labels = [int(match.group(1)) for match in _OUTLINE_HEADING.finditer(body)]
    return not labels or labels == [position]


def visible_copy(raw, snapshot, facts, required):
    """Bind newly typeset text to the reviewed declaration for its slot."""
    if not required:
        return single_prose(raw, snapshot), []
    value = strict_object(raw, singleton_audit=[])
    if 'questions' in value:
        return validate_questions(value), []
    if set(value) not in ({'prompt', 'copy'}, {'prompt', 'copy', 'review_signal'}) or not isinstance(value['copy'], list):
        raise FormatError('PLANNER_VISIBLE_COPY_REQUIRED')
    body = value['prompt']
    if not isinstance(body, str):
        raise FormatError('PLANNER_SINGLE_PROMPT_REQUIRED')
    slots = re.findall(r'\[\[copy:([1-9]\d*)\]\]', body)
    if set(slots) != {str(i) for i in range(1, len(value['copy']) + 1)} or len(slots) != len(value['copy']):
        raise FormatError('PLANNER_VISIBLE_COPY_SLOTS')
    for entry in value['copy']:
        if (not isinstance(entry, dict) or set(entry) != {'text', 'fact_id'}
                or not isinstance(entry['text'], str) or not entry['text'].strip()
                or '[[' in entry['text']):
            raise FormatError('PLANNER_VISIBLE_COPY_REQUIRED')
        fact_id = entry['fact_id']
        if fact_id is not None and (not isinstance(fact_id, str) or fact_id not in facts
                or entry['text'] not in facts[fact_id]):
            raise FormatError('PLANNER_VISIBLE_COPY_EVIDENCE')
    rendered = re.sub(r'\[\[copy:([1-9]\d*)\]\]', lambda m: value['copy'][int(m.group(1)) - 1]['text'], body)
    if '[[copy:' in rendered:
        raise FormatError('PLANNER_VISIBLE_COPY_SLOTS')
    candidate = {'prompt': rendered, **({'review_signal': value['review_signal']} if 'review_signal' in value else {})}
    return single_prose(json.dumps(candidate, ensure_ascii=False), snapshot), value['copy']


def validate_outline(value, snapshot):
    if isinstance(value, dict) and 'questions' in value:
        return validate_questions(value)
    paths = schema_paths(value, OUTLINE_SCHEMA)
    if paths:
        raise FormatError('PLANNER_OUTLINE_INVALID', paths)
    count = snapshot['image_count']
    outline = value['outline']
    if len(outline) > count:
        raise FormatError('PLANNER_OUTLINE_COUNT', [('outline',)])
    bad = [['outline', index] for index in range(count) if index >= len(outline)
        or not outline_label_matches(outline[index], index + 1)
        or outline[index] in outline[:index]]
    if bad:
        raise FormatError('PLANNER_OUTLINE_COUNT', bad)
    from .assembly import fixed_canvas_conflict
    conflicts = [(key,) for key in ('visual_spec', 'shared_rules') if fixed_canvas_conflict(value[key], snapshot)]
    conflicts += [('outline', i) for i, task in enumerate(value['outline']) if fixed_canvas_conflict(task, snapshot)]
    if conflicts:
        raise FormatError('PLANNER_FIXED_CANVAS_CONFLICT', conflicts)
    result = deepcopy(value)
    try:
        result['visual_spec'] = normalize_visual(value['visual_spec'])
    except FormatError as error:
        raise FormatError(error.code, [('visual_spec', *path) for path in error.paths]) from error
    return result


def single_prose(raw, snapshot):
    """Unwrap only unambiguous transport packaging, never rewrite creative prose."""
    body = raw.strip()
    fenced = re.fullmatch(r'```(?:json|markdown|text)?\s*\n(.*?)\n```', body, re.S)
    if fenced:
        body = fenced[1].strip()
    if re.match(r'\[\s*(?:"|\{|\[|\])', body):
        value = json.loads(body)
        if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], str):
            raise FormatError('PLANNER_SINGLE_PROMPT_REQUIRED')
        body = value[0]
    if body.startswith('{'):
        value = strict_object(body)
        if 'questions' in value:
            return validate_questions(value)
        if set(value) == {'prompt', 'review_signal'} and isinstance(value['prompt'], str):
            body = single_prose(value['prompt'], snapshot)
            signal = value['review_signal']
            if (not isinstance(body, str) or not isinstance(signal, dict)
                    or set(signal) != {'positions', 'reason'}
                    or not valid_positions(signal['positions'], snapshot['image_count'])
                    or len(signal['positions']) < 2
                    or not isinstance(signal['reason'], str) or not signal['reason'].strip()):
                raise FormatError('PLANNER_SET_REVIEW_SIGNAL_INVALID')
            return {'prompt': body, 'review_signal': signal}
        if set(value) == {'prompt'} and isinstance(value['prompt'], str):
            body = value['prompt']
        elif set(value) == {'prompts'} and isinstance(value['prompts'], list) and len(value['prompts']) == 1:
            body = value['prompts'][0]
        else:
            raise FormatError('PLANNER_SINGLE_PROMPT_REQUIRED')
    if not isinstance(body, str) or not body.strip():
        raise FormatError('ECOM_PLAN_EMPTY_OUTPUT')
    # Only executable identity is checked here. Prose quality is assessed with
    # the original professional method, not an expanding list of banned words.
    if prompt_positions({'prompts': [body]}, {**snapshot, 'image_count': 1}):
        raise FormatError('PLANNER_PROMPT_REFERENCE_INVALID')
    return body


def valid_positions(positions, count):
    return (isinstance(positions, list) and bool(positions)
        and all(type(position) is int and 1 <= position <= count for position in positions)
        and len(set(positions)) == len(positions))


class PositionDelivery:
    async def _position_stage(self, row, lease, original, integration, evidence, messages, refs, urls, validator):
        snapshot = evidence['input_snapshot']
        count = snapshot['image_count']
        persisted = deepcopy((evidence.get('stage_drafts') or {}).get('3'))
        state = deepcopy(persisted) if persisted else {
            'format_version': FORMAT_VERSION, 'delivery_version': POSITION_VERSION,
            'positions': {str(position): {} for position in range(1, count + 1)}}
        if state.get('delivery_version') != POSITION_VERSION:
            raise FormatError('PLANNER_RESOURCE_VERSION_MISMATCH')
        if state.get('repair_exhausted'):
            raise FormatError('PLANNER_DELIVERY_REPAIR_EXHAUSTED')
        positions = state['positions']

        async def persist():
            nonlocal persisted
            await self._persist_format(row, lease, 3, persisted, state)
            persisted = deepcopy(state)

        if not persisted:
            await persist()
        prompt = original + '\n\n【最终平台交付协议】\n' + integration
        product = evidence['product_selling_points']
        copy_required = evidence.get('require_visible_copy', False)
        copy_facts = {fact['id']: fact['statement'] for fact in product['facts'] if fact['status'] == 'usable'
            and any(source['kind'] in {'image_observation', 'image_text', 'user_statement'} for source in fact['sources'])}
        common = user_content({'stage': 3, **model_input(snapshot, messages, refs),
            'product_selling_points': evidence['product_selling_points'],
            'visual_direction': evidence['visual_direction'],
            **({'visible_copy_evidence': copy_facts} if copy_required else {})}, refs, urls, stable_keys=True)
        prefix_hash = text_hash(json.dumps([prompt, common], ensure_ascii=False))
        save_lock = asyncio.Lock()
        concurrent = asyncio.Semaphore(MAX_POSITION_CONCURRENCY)

        async def call(position, details):
            # Repeated requests share exact instructions, JSON and media order.
            # Only the suffix varies, including repair details on retries.
            cancellation = getattr(self.owner, 'cancellation_event', None)
            if cancellation is not None and cancellation.is_set():
                raise asyncio.CancelledError()
            return await self._call(row, lease, 3, prompt, [
                {'role': 'developer', 'content': prompt},
                {'role': 'user', 'content': [*common, {
                    'type': 'input_text', 'text': json.dumps(details, ensure_ascii=False)}]},
            ], position=position)

        async def write(position):
            nonlocal persisted, state, positions
            key = str(position)
            entry = positions[key]
            if isinstance(entry.get('prompt'), str):
                single_prose(entry['prompt'], snapshot)
                return
            for errors in range(entry.get('invalid_deliveries', 0), 3):
                details = {'current_position': position,
                    'current_task': evidence['visual_direction']['outline'][position - 1]}
                if entry.get('error'):
                    details.update(previous_draft=entry.get('raw'), validation_error=entry['error'])
                async with concurrent:
                    raw, usage = await call(position, details)
                usage = {**usage, 'position': position, 'delivery_version': POSITION_VERSION,
                    'response_sha256': text_hash(raw)}
                async with save_lock:
                    next_state = deepcopy(state)
                    next_positions = next_state['positions']
                    next_state.setdefault('attempt_outputs', []).append({'position': position, 'raw': raw,
                        'usage': deepcopy(usage)})
                    try:
                        candidate, copy = visible_copy(raw, snapshot, copy_facts, copy_required)
                        if isinstance(candidate, dict) and 'questions' in candidate:
                            next_positions[key] = {'questions': candidate['questions'], 'raw': raw}
                        else:
                            signal = None
                            if isinstance(candidate, dict):
                                if 'review_signal' not in candidate or position not in candidate['review_signal']['positions']:
                                    raise FormatError('PLANNER_SET_REVIEW_SIGNAL_INVALID')
                                signal = candidate['review_signal']
                                candidate = candidate['prompt']
                            if any(value.get('prompt') == candidate for other, value in positions.items() if other != key):
                                raise FormatError('PLANNER_DUPLICATE_POSITION_DRAFT')
                            from .assembly import assemble_prompts
                            assemble_prompts({'prompts': [candidate]}, {**snapshot, 'image_count': 1},
                                evidence['product_selling_points'], evidence['visual_direction'])
                            next_positions[key] = {'prompt': candidate, 'raw': raw, 'attempt_id': usage['attempt_id'],
                                'shared_prefix_sha256': prefix_hash,
                                'request_context_sha256': text_hash(json.dumps([prefix_hash, details], ensure_ascii=False)),
                                'response_sha256': text_hash(raw), 'visible_copy': copy}
                            recheck = entry.get('recheck_signal')
                            if signal and recheck:
                                signal = {'positions': sorted(set(signal['positions']) | set(recheck['positions'])),
                                    'reason': recheck['reason'] + '\n' + signal['reason']}
                            if signal or recheck:
                                next_positions[key]['review_signal'] = signal or recheck
                    except ValueError as error:
                        entry = next_positions[key] = {**entry, 'raw': raw, 'error': str(error), 'invalid_deliveries': errors + 1}
                        await self._save_attempt(row, lease, 3, {**usage, 'validation_error': str(error)},
                            'position_invalid', 0, draft=next_state)
                        state, positions = next_state, next_positions
                        persisted = deepcopy(state)
                        continue
                    await asyncio.to_thread(lambda: self.scope.rpc('finish_ecom_position_attempt', {
                        'p_plan_id': row['id'], 'p_lease_token': lease, 'p_attempt_id': usage['attempt_id'],
                        'p_usage': usage, 'p_expected_draft': persisted, 'p_draft': next_state,
                    }).execute().data)
                    state, positions = next_state, next_positions
                    persisted = deepcopy(state)
                    return
            raise FormatError('PLANNER_DELIVERY_REPAIR_EXHAUSTED')

        async def review(signals):
            nonlocal persisted
            targets = sorted({position for signal in signals for position in signal['positions']})
            details = {'mode': 'targeted_set_review', 'signals': signals,
                'final_drafts': {str(position): positions[str(position)]['prompt'] for position in targets}}
            raw, usage = await call(0, details)
            try:
                report = strict_object(raw)
                repairs = report.get('repair_positions')
                if (set(report) != {'repair_positions', 'reason'} or not isinstance(repairs, list)
                        or (repairs and (not valid_positions(repairs, count) or not set(repairs) <= set(targets)))
                        or not isinstance(report['reason'], str) or not report['reason'].strip()):
                    raise FormatError('PLANNER_SET_REVIEW_INVALID')
            except ValueError as error:
                state.setdefault('review_attempts', []).append({'raw': raw, 'error': str(error)})
                await self._save_attempt(row, lease, 3, {**usage, 'position': 0, 'validation_error': str(error)},
                    'set_review_invalid', 0, draft=state)
                persisted = deepcopy(state)
                raise
            state.setdefault('reviews', []).append({'targets': targets, 'report': report,
                'draft_sha256': {str(position): text_hash(positions[str(position)]['prompt']) for position in targets}})
            for position in targets:
                positions[str(position)].pop('review_signal', None)
            for position in repairs:
                key = str(position)
                positions[key] = {'raw': positions[key]['prompt'], 'error': report['reason'],
                    'recheck_signal': {'positions': targets, 'reason': report['reason']}}
            await self._save_attempt(row, lease, 3, {**usage, 'position': 0}, 'set_review', 0, draft=state)
            persisted = deepcopy(state)

        def review_limit_reached():
            return len(state.get('reviews', [])) + len(state.get('review_attempts', [])) >= 3

        while True:
            if any(entry.get('recheck_signal') for entry in positions.values()) and review_limit_reached():
                raise FormatError('PLANNER_DELIVERY_REPAIR_EXHAUSTED')
            outcomes = await asyncio.gather(*(write(position) for position in range(1, count + 1)
                if not positions[str(position)].get('questions')), return_exceptions=True)
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    raise outcome
            questions = []
            seen = set()
            for entry in positions.values():
                for question in entry.get('questions', []):
                    if question not in seen:
                        questions.append(question)
                        seen.add(question)
            if questions:
                return validate_questions({'questions': questions[:3]}), {'local_draft': state}
            signals = [entry['review_signal'] for entry in positions.values() if entry.get('review_signal')]
            if not signals:
                break
            if review_limit_reached():
                raise FormatError('PLANNER_DELIVERY_REPAIR_EXHAUSTED')
            await review(signals)
        value = {'prompts': [positions[str(position)]['prompt'] for position in range(1, count + 1)]}
        result = validator(value)
        result['planning_review_scope'] = {'method': 'position_self_check_and_programmatic_assembly',
            'triggered_reviews': state.get('reviews', []), 'generated_images': False}
        return result, {'local_draft': state}
