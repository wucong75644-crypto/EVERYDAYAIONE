"""Page-only delivery recovery; provider IO, leases and settlement stay shared."""
from copy import deepcopy
import asyncio
import json

from .contracts import VISUAL_SECTIONS, text_hash, validate_schema
from .designs import DesignsOutput, DesignValidationError
from .format_delivery import (FORMAT_VERSION, PROMPTS_VERSION, PROMPTS_SCHEMA, FormatError,
    decode_formatted, strict_object, normalize_visual, partial_visual_sections, render_visual,
    product_paths, schema_paths, merge_values, preserve_string_tokens, validate_questions, _visual_parts)
from .inputs import model_input, user_content
from .recovery import error_facts
from .delivery import decode_delivery


class PageDelivery:
    def _page_schema(self, stage, evidence):
        if stage == 1:
            # Fixed metadata is server supplied; the business schema stays v3.
            schema = deepcopy(evidence['product_schema'])
            schema['properties']['schema_version'] = {'const': 'product-selling-points.v3'}
            if 'schema_version' not in schema['required']:
                schema['required'].append('schema_version')
            return schema
        return PROMPTS_SCHEMA if self.delivery_version == PROMPTS_VERSION else DesignsOutput.model_json_schema()

    def _page_candidate(self, raw, stage, evidence):
        if stage == 2:
            if isinstance(raw, dict) or isinstance(raw, str) and raw.lstrip().startswith(('{', '```json')):
                return validate_questions(strict_object(raw) if isinstance(raw, str) else raw), {}
            visual = normalize_visual(raw)
            return visual, {'format_version': FORMAT_VERSION, 'normalization':
                [{'operation': 'visual_headings'}] if visual != raw else [],
                'response_sha256': text_hash(raw), 'canonical_sha256': text_hash(visual)}
        schema = self._page_schema(stage, evidence)
        if stage == 3 and self.delivery_version != PROMPTS_VERSION and isinstance(raw, str) and raw.lstrip().startswith('{'):
            legacy, legacy_audit = decode_delivery(raw)
            value, audit = decode_formatted(legacy, schema)
            audit['response_sha256'] = text_hash(raw)
            if legacy_audit['trailing_characters']:
                audit['normalization'].append({'operation': 'legacy_advisory_tail',
                    'characters': legacy_audit['trailing_characters']})
        else:
            value, audit = decode_formatted(raw, schema)
        if stage == 3 and 'questions' in value and 'prompts' not in value and self.delivery_version == PROMPTS_VERSION:
            return validate_questions(value), audit
        if stage == 1:
            fixed = 'product-selling-points.v3'
            minimal = self.delivery_version == PROMPTS_VERSION
            if not minimal and value.get('schema_version') not in {None, fixed}:
                raise FormatError('PLANNER_STAGE_VERSION_CONFLICT')
            if value.get('schema_version') != fixed:
                audit['normalization'].append({'path': ['schema_version'], 'operation': 'fixed_metadata'})
                value['schema_version'] = fixed
            # Only explicitly nullable, absent fields receive null, never facts/specs.
            def defaults(obj, spec, path=()):
                if '$ref' in spec:
                    spec = schema['$defs'][spec['$ref'].rsplit('/', 1)[1]]
                if isinstance(obj, dict):
                    for key, child in spec.get('properties', {}).items():
                        if key not in obj and key in spec.get('required', []):
                            try:
                                validate_schema(None, child, schema)
                            except ValueError:
                                continue
                            obj[key] = None
                            audit['normalization'].append({'path': [*path, key], 'operation': 'nullable_default'})
                        if key in obj:
                            defaults(obj[key], child, (*path, key))
                elif isinstance(obj, list):
                    for i, child in enumerate(obj):
                        defaults(child, spec.get('items', {}), (*path, i))
            if minimal:
                defaults(value, schema)
        if stage == 3 and self.delivery_version == PROMPTS_VERSION and isinstance(value.get('prompts'), list):
            count = evidence['input_snapshot']['image_count']
            if len(value['prompts']) > count:
                audit['normalization'].append({'path': ['prompts'], 'operation': 'unrequested_positions',
                    'count': len(value['prompts']) - count})
                value['prompts'] = value['prompts'][:count]
        audit['canonical_sha256'] = text_hash(json.dumps(value, ensure_ascii=False))
        return value, audit

    def _page_paths(self, stage, candidate, evidence, error):
        if stage == 3 and self.delivery_version == PROMPTS_VERSION and isinstance(candidate, dict) and 'questions' in candidate and 'prompts' in candidate:
            # An explicitly incomplete response cannot become ready by deleting its questions.
            return []
        if stage == 2:
            import re
            from .assembly import fixed_canvas_conflict
            sections = partial_visual_sections(candidate) if isinstance(candidate, str) else [None] * 8
            return [['sections', i] for i, body in enumerate(sections) if not body
                or not re.sub(r'[#*\s-]', '', body) or fixed_canvas_conflict(body, evidence['input_snapshot'])]
        if isinstance(error, (FormatError, DesignValidationError)) and getattr(error, 'paths', None):
            return error.paths
        if isinstance(error, DesignValidationError):
            return [issue['path'] for issue in error.issues]
        if not isinstance(candidate, dict):
            return []
        schema = self._page_schema(stage, evidence)
        if stage == 1:
            return product_paths(candidate, schema, evidence['input_snapshot'])
        return schema_paths(candidate, schema)

    async def _persist_format(self, row, lease, stage, previous, state):
        result = await asyncio.to_thread(lambda: self.scope.rpc('persist_ecom_plan_format_state', {
            'p_plan_id': row['id'], 'p_lease_token': lease, 'p_stage': stage,
            'p_expected_draft': previous, 'p_draft': state}).execute().data)
        if result.get('outcome') != 'saved':
            raise FormatError('ECOM_PLAN_DRAFT_CONFLICT')

    async def _page_stage(self, row, lease, stage, original, integration, evidence, messages, refs, image_urls, validator):
        state = deepcopy((evidence.get('stage_drafts') or {}).get(str(stage)))
        persisted = deepcopy(state)
        async def persist():
            nonlocal persisted
            await self._persist_format(row, lease, stage, persisted, state)
            persisted = deepcopy(state)
        usage = None
        if not state:
            body = {'stage': stage, **model_input(evidence['input_snapshot'], messages, refs)}
            if stage == 1:
                body['product_schema'] = evidence['product_schema']
            else:
                body['product_selling_points'] = evidence['product_selling_points']
            if stage == 3:
                body['visual_direction'] = evidence['visual_direction']
                body['output_json_schema'] = self._page_schema(stage, evidence)
            prompt = original + '\n\n【最终平台交付协议】\n' + integration
            raw, usage = await self._call(row, lease, stage, prompt, [
                {'role': 'developer', 'content': prompt}, {'role': 'user', 'content': user_content(body, refs, image_urls)}])
            state = {'output': raw, 'format_version': FORMAT_VERSION, 'format_attempted': False, 'repair_round': 0}
        state.setdefault('original_output', deepcopy(state.get('output')))
        state.setdefault('original_response_sha256', text_hash(state['original_output'] if isinstance(state['original_output'], str)
            else json.dumps(state['original_output'], ensure_ascii=False)))
        while True:
            raw = state.get('output')
            candidate = None
            audit = {}
            try:
                candidate, audit = self._page_candidate(raw, stage, evidence)
                if audit:
                    history = state.setdefault('audit_history', [])
                    if not history or history[-1] != audit:
                        history.append(deepcopy(audit))
                # A question-only response is terminal, never an executable partial plan.
                result = candidate if isinstance(candidate, dict) and set(candidate) == {'status', 'questions'} else validator(candidate)
                state.update(format_version=FORMAT_VERSION, canonical=candidate, audit=audit)
                if usage is not None:
                    if self.delivery_version == PROMPTS_VERSION or audit.get('normalization'):
                        await persist()
                    return result, {**usage, 'delivery_version': self.delivery_version, **audit}
                await persist()
                return result, {'local_draft': state}
            except ValueError as error:
                paths = self._page_paths(stage, candidate if candidate is not None else raw, evidence, error)
                paths = [list(path) for path in dict.fromkeys(tuple(path) for path in paths)]
                state.update(format_version=FORMAT_VERSION, validation_error=str(error),
                    canonical=candidate, repair_paths=paths)
                if usage is not None:
                    await self._save_attempt(row, lease, stage, {**usage, **audit, 'validation_error': str(error)},
                        'format_repair', state.get('repair_round', 0), draft=state)
                    persisted = deepcopy(state)
                    usage = None
                else:
                    await persist()
            # A malformed wrapper gets one formatting call for this saved draft,
            # shared across process restarts and delivery windows. No images/Skill.
            known_sections = stage == 2 and isinstance(raw, str) and any(partial_visual_sections(raw))
            # A syntactically valid object can still package later prompt strings
            # as object keys/values. One token-preserving format pass can recover
            # their original order without guessing how to extract the prose.
            misplaced_prompts = (stage == 3 and self.delivery_version == PROMPTS_VERSION
                and isinstance(candidate, dict) and isinstance(candidate.get('prompts'), list)
                and len(candidate['prompts']) < evidence['input_snapshot']['image_count']
                and any(key != 'prompts' and value is not None for key, value in candidate.items())
                and 'questions' not in candidate)
            formatting = (candidate is None and not known_sections or misplaced_prompts) and not state.get('format_attempted')
            before_recovery = deepcopy(state)
            if formatting:
                state['format_attempted'] = True
            else:
                if not paths or state.get('repair_round', 0) >= 2:
                    raise FormatError('PLANNER_DELIVERY_REPAIR_EXHAUSTED')
                state['repair_round'] = state.get('repair_round', 0) + 1
            await persist()
            async def recovery_call(prompt, sent):
                try:
                    return await self._call(row, lease, stage, prompt, sent)
                except Exception as error:
                    code, _category, definitely_rejected = error_facts(error)
                    if definitely_rejected or code in {'ECOM_PLAN_RETRY_EXHAUSTED', 'ECOM_PLAN_PARENT_BUDGET_EXHAUSTED'}:
                        # No completed formatting/repair occurred. A future delivery
                        # window can use this action; uncertainty never resets it.
                        state['format_attempted'] = before_recovery.get('format_attempted', False)
                        state['repair_round'] = before_recovery.get('repair_round', 0)
                        await persist()
                    raise
            if formatting:
                prompt = ('你是交付格式整理器。只整理原稿语法或Markdown标题包装，保留所有正文、数值、事实和顺序。'
                    '不重新策划、不添加内容、不重写任何有效文字。JSON只输出一个完整对象，保持原有字符串的顺序及内容；'
                    '视觉定位只整理为指定八节Markdown。')
                body = {'original_draft': raw, 'error': state['validation_error'],
                    'required_sections': list(VISUAL_SECTIONS) if stage == 2 else None,
                    'output_json_schema': self._page_schema(stage, evidence) if stage != 2 else None,
                    'image_count': evidence['input_snapshot']['image_count']}
                formatted, usage = await recovery_call(prompt, [
                    {'role': 'developer', 'content': prompt}, {'role': 'user', 'content': json.dumps(body, ensure_ascii=False)}])
                state['format_output'] = formatted
                state['format_input_sha256'] = text_hash(raw)
                try:
                    if stage == 2:
                        # Adding headings is allowed; every original nonheading line stays verbatim.
                        import re
                        lines = lambda text: [line for line in text.splitlines() if line.strip() and not re.match(r'^\s*#', line)]
                        if lines(raw) != lines(formatted):
                            raise FormatError('PLANNER_VALID_CONTENT_CHANGED')
                    else:
                        strict_object(formatted)
                        formatted = preserve_string_tokens(raw, formatted)
                        strict_object(formatted)
                except ValueError as error:
                    await self._save_attempt(row, lease, stage, {**usage, 'validation_error': str(error)},
                        'format_repair', 0, draft=state)
                    persisted = deepcopy(state)
                    usage = None
                    continue
                state['output'] = formatted
            else:
                prompt, body = self._page_repair_request(stage, candidate, raw, paths, evidence, original, integration)
                output, usage = await recovery_call(prompt, [
                    {'role': 'developer', 'content': prompt}, {'role': 'user', 'content': user_content(body, refs, image_urls)}])
                try:
                    state['output'] = self._page_merge_repair(stage, candidate, raw, paths, output, evidence)
                except ValueError as error:
                    await self._save_attempt(row, lease, stage, {**usage, 'validation_error': str(error)},
                        'partial_repair', state['repair_round'], draft=state)
                    persisted = deepcopy(state)
                    usage = None
                    continue

    def _page_repair_request(self, stage, candidate, raw, paths, evidence, original, integration):
        body = {'settings_and_sources': model_input(evidence['input_snapshot'],
            evidence['input_snapshot']['messages'], evidence['input_snapshot']['references']),
            'original_draft': candidate if candidate is not None else raw,
            'targets_in_order': paths, 'product_selling_points': evidence.get('product_selling_points'),
            'visual_direction': evidence.get('visual_direction')}
        if stage == 3 and self.delivery_version == PROMPTS_VERSION and self._prompt_targets(paths):
            output = '只返回{"prompts":["目标位置的完整正文"]}，顺序与targets_in_order相同，只补写这些位置。'
        elif stage == 2:
            output = '只返回{"sections":["目标节的正文"]}，顺序与targets_in_order相同，不含标题。'
        else:
            output = ('只返回{"values":[替换值]}，顺序与targets_in_order相同，程序负责合并，'
                '不输出path/op或完整对象。多余字段以null表示删除，不能删除必填字段。')
            body['validation_schema'] = self._page_schema(stage, evidence)
        return original + '\n\n' + integration + '\n\n【本次只修补】\n' + output + '保留其他全部有效内容。', body

    def _page_merge_repair(self, stage, candidate, raw, paths, output, evidence):
        repair = strict_object(output)
        if stage == 2 or stage == 3 and self.delivery_version == PROMPTS_VERSION and self._prompt_targets(paths):
            key = 'sections' if stage == 2 else 'prompts'
            if set(repair) != {key} or not isinstance(repair[key], list) or len(repair[key]) != len(paths):
                raise FormatError('PLANNER_REPAIR_TARGET_COUNT')
            if any(not isinstance(value, str) or not value.strip() for value in repair[key]):
                raise FormatError('PLANNER_REPAIR_TEXT_REQUIRED')
            if stage == 2:
                values = partial_visual_sections(raw)
                for path, value in zip(paths, repair[key]):
                    values[path[1]] = value
                if any(value is None for value in values):
                    raise FormatError('PLANNER_REPAIR_TARGET_COUNT')
                return render_visual(values, _visual_parts(raw)[0])
            result = deepcopy(candidate)
            for path, value in zip(paths, repair[key]):
                if len(path) != 2 or path[0] != 'prompts':
                    raise FormatError('PLANNER_REPAIR_TARGET_UNLOCATABLE')
                index = path[1]
                while len(result['prompts']) <= index:
                    result['prompts'].append(None)
                result['prompts'][index] = value
            return result
        if set(repair) != {'values'}:
            raise FormatError('PLANNER_REPAIR_FIELDS_INVALID')
        return merge_values(candidate, paths, repair['values'], self._page_schema(stage, evidence))

    @staticmethod
    def _prompt_targets(paths):
        return bool(paths) and all(len(path) == 2 and path[0] == 'prompts' and isinstance(path[1], int) for path in paths)
