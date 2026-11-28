"""Model-facing image handles, resolved only inside their input snapshot.

Native checkpoints and tools retain canonical identities. This adapter changes
neither provenance nor authorization and never interprets user wording.
"""
import copy
import hashlib
import json

from .contracts import TaskError


class ImageReferences:
    def __init__(self, snapshot):
        self.references = {}
        self.reverse = {}
        self.candidates = []
        self.current_message = snapshot['input_message_id']
        self.current_images = []
        self.reference_format = False
        self.ready_image_refs = None
        self.selection_action = None
        self.example_image_ref = None
        self.scope = hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()[:10]
        from .content_reference_catalog import ContentReferences
        self.contents = ContentReferences(self.scope)
        for group in snapshot.get('saved_groups', ()):
            self.contents.add(group)
        for row in snapshot.get('requests', ()):
            for image in row.get('images', ()):
                handle = self.add(image['reference'])
                if row['message_id'] == self.current_message:
                    self.current_images.append(image['reference'])
                self.candidates.append({'image_ref': handle, 'name': image.get('name'),
                    'turn_number': row.get('turn_number'), 'provenance': 'quoted_image' if image.get('client_reference') else 'user_upload'})
        for image in snapshot.get('image_results', ()):
            handle = self.add({'message_id': image['source_message_id'], 'content_index': image['content_index']})
            for alias in image.get('displayed_in', ()):
                identity = self.identity(alias)
                if identity is not None:
                    self.reverse[identity] = handle
            entry = {'image_ref': handle, 'name': image.get('name'),
                'turn_number': image.get('original_turn', image.get('generated_for_turn')),
                'provenance': image.get('origin_kind', 'generated_result'),
                'displayed_in': [{'display_message_id': a['message_id'],
                                 'display_turn': a.get('turn_number')} for a in image.get('displayed_in', ())]}
            existing = next((c for c in self.candidates if c['image_ref'] == handle), None)
            if existing is None:
                self.candidates.append(entry)
            else:
                existing.update(entry)
        for image in snapshot.get('_delivered_image_changes', ()):
            handle = self.add(image['reference'])
            self.candidates.append({'image_ref': handle, 'name': image.get('name'), 'provenance': 'delivered_attachment'})

    @staticmethod
    def identity(reference):
        if isinstance(reference.get('message_id'), str) and type(reference.get('content_index')) is int:
            return ('message', reference['message_id'], reference['content_index'])
        for key in ('resource_ref', 'asset_id', 'file_id'):
            if isinstance(reference.get(key), str):
                return (key, reference[key])
        return None

    def add(self, reference):
        pair = self.identity(reference)
        if pair is None:
            return
        if pair not in self.reverse:
            handle = f'img_{self.scope}_{len(self.reverse) + 1}'
            self.reverse[pair] = handle
            self.references[handle] = ({'message_id': pair[1], 'content_index': pair[2]} if pair[0] == 'message'
                                       else {pair[0]: pair[1]})
        return self.reverse[pair]

    def field(self, *, message_only=False):
        handles = [handle for handle, ref in self.references.items() if not message_only or 'message_id' in ref]
        return {'type': 'string', **({'enum': handles} if self.references else {}),
                'description': '复制当前图片目录的image_ref。短引用绑定当前输入快照，不能沿用其他轮次的编号。'}

    def decode(self, value, path='$'):
        if isinstance(value, list):
            return [self.decode(item, f'{path}[{index}]') for index, item in enumerate(value)]
        if not isinstance(value, dict):
            return value
        result = {key: self.decode(item, path + '.' + key) for key, item in value.items() if key != 'image_ref'}
        result = self.contents.decode(result, path)
        canvas = result.pop('canvas_ref', None)
        if canvas is not None:
            handle = canvas
            reference = self.references.get(handle) if isinstance(handle, str) else None
            if reference is None:
                raise TaskError('IMAGE_REFERENCE_UNKNOWN_OR_STALE')
            if result.get('canvas_image') is not None or result.get('canvas_resource') is not None:
                raise TaskError('IMAGE_REFERENCE_IDENTITY_CONFLICT')
            if 'resource_ref' in reference:
                result['canvas_resource'] = reference['resource_ref']
            elif reference in self.current_images:
                result['canvas_image'] = self.current_images.index(reference) + 1
            else:
                raise TaskError('IMAGE_CANVAS_REFERENCE_NOT_CURRENT')
        if 'image_ref' not in value:
            return result
        if value['image_ref'] is None and any(result.get(key) is not None for key in ('resource_ref', 'file_id', 'asset_id')):
            return result  # Null optional selector in an existing native file form.
        reference = self.references.get(value['image_ref']) if isinstance(value['image_ref'], str) else None
        if reference is None:
            raise TaskError('IMAGE_REFERENCE_UNKNOWN_OR_STALE')
        locator_fields = {'source_reference', 'message_id', 'content_index', 'source_message_id', 'source_content_index',
                          'resource_ref', 'file_id', 'asset_id'}
        if any(result.get(key) is not None for key in locator_fields):
            raise TaskError('IMAGE_REFERENCE_IDENTITY_CONFLICT')
        for key in locator_fields:
            result.pop(key, None)
        if result.get('kind') == 'image':
            if 'message_id' not in reference:
                raise TaskError('IMAGE_REFERENCE_REQUIRES_CURRENT_TARGET')
            result.update(source_message_id=reference['message_id'], content_index=reference['content_index'])
        elif 'edit_instruction' in result:
            if 'message_id' in reference:
                result.update(source_message_id=reference['message_id'], source_content_index=reference['content_index'])
            else:
                result['source_reference'] = reference
        else:
            result.update(reference)
        return result

    def decode_call(self, name, value):
        result = self.decode(value)
        if name == 'save_task_text':
            from .content_update_wire import native_updates
            from .reference_prompt_format import decode_save
            result = decode_save(native_updates(result), lambda identity, index: self.contents.items.get((identity, index)))
        return result

    def encode(self, value):
        if isinstance(value, list):
            return [self.encode(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: copy.deepcopy(item) if key in {'message_source', 'source_prompt'} else self.encode(item)
                  for key, item in value.items()}
        result = self.contents.encode(result)
        if 'format_contract' in value and isinstance(result.get('content_source'), dict):
            checksum = result['content_source'].pop('sha256', None)
            if checksum is not None:
                result['content_sha256'] = checksum  # Integrity evidence is not a selector field.
        if (value.get('format_contract') or {}).get('profile_id') == 'reference-prompts-v1' and isinstance(value.get('item'), dict):
            from .reference_prompt_format import encode_item, MODEL_GUIDANCE
            result['item'] = encode_item(result['item'])
            result['format_contract']['model_input_guidance'] = MODEL_GUIDANCE
        # A message ID by itself still identifies a request or a saved document.
        # Only a complete image locator is projected to a handle.
        mid_key = 'message_id' if 'message_id' in value else 'source_message_id'
        index_key = 'source_content_index' if 'source_content_index' in value else 'content_index'
        reference = ({'message_id': value.get(mid_key), 'content_index': value.get(index_key)}
                     if isinstance(value.get(mid_key), str) and type(value.get(index_key)) is int else value)
        if 'edit_instruction' in value and isinstance(value.get('source_reference'), dict):
            reference = value['source_reference']
            result.pop('source_reference', None)
        handle = self.reverse.get(self.identity(reference))
        if handle:
            result.pop(mid_key, None)
            result.pop(index_key, None)
            for key in ('resource_ref', 'asset_id', 'file_id'):
                result.pop(key, None)
            result.pop('image_reference', None)
            result['image_ref'] = handle
        return result

    def schema(self, value):
        if isinstance(value, list):
            return [self.schema(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: self.schema(item) for key, item in value.items() if key not in {'x-content-profile', 'x-reference-kind'}}
        if isinstance(result.get('description'), str):
            from .content_objects import MATERIAL_REFERENCE_GUIDANCE
            result['description'] = result['description'].replace(MATERIAL_REFERENCE_GUIDANCE,
                'reference是JSON对象；图片填写{"image_ref":"当前目录编号"}，不是编号字符串；其他已核验文件可使用resource_ref、file_id或asset_id。')
            result['description'] = result['description'].replace(
                '从该图的 reference 原样复制 message_id 和 content_index 到对应字段。', '复制该图的image_ref。').replace(
                '编辑图片统一填写 source_message_id、source_content_index 和 edit_instruction；',
                '编辑图片统一填写image_ref和edit_instruction；')
            result['description'] = result['description'].replace('content_index 是消息内的位置。', '')
        function = result.get('function')
        if isinstance(function, dict) and self.reference_format:
            from .content_objects import IMAGE_REVISION_GUIDANCE
            function['description'] = function.get('description', '').replace(IMAGE_REVISION_GUIDANCE,
                IMAGE_REVISION_GUIDANCE.replace('display_prefix', 'presentation').replace('body', 'prompt').replace('artifact_id', 'content_ref'))
        if isinstance(function, dict) and function.get('name') == 'save_task_text':
            profile = value.get('function', {}).get('parameters', {}).get('x-content-profile')
            if profile is not None:
                function['parameters']['x-content-profile'] = profile
            if 'document' in function['parameters'].get('properties', {}):
                from .content_objects import creation_examples
                document_schema = function['parameters']['properties']['document']
                reference = function['parameters'].get('x-content-profile') == 'reference-prompts-v1'
                already_public = 'reference_analysis' in document_schema.get('properties', {})
                example = creation_examples(reference)[0]
                if not reference and not already_public:
                    function['description'] += (' 新建参数的对象结构示例（内容须按实际要求填写，素材另按目录绑定）：'
                    + json.dumps(example, ensure_ascii=False)
                    + '。items每项的标题字段固定为title，执行正文固定为body。'
                    '如用户要求等待确认，confirmation是JSON对象，例如'
                    '{"tool":"generate_image","wait_for":[1]}，不是序列化后的字符串；等待条目按实际要求填写。')
            from .reference_prompt_format import project_tool
            result['function'] = function = project_tool(function)
            workflow = function.pop('_content_workflow', None)
            if workflow:
                from .reference_prompt_format import workflow_examples
                function['parameters']['examples'] = workflow_examples(function['parameters'].get('examples', []), workflow['state'])
                updates = function['parameters'].get('properties', {}).get('updates', {}).get('items')
                if updates is not None:
                    for item in workflow['state'].get('items', ()):
                        fields = item.get('required_update_fields')
                        if fields:
                            updates.setdefault('allOf', []).append({
                                'if': {'required': ['item_index'], 'properties': {'item_index': {
                                    'const': item['content_source']['item_index']}}},
                                'then': {'properties': {'item': {'required': fields}}}})
                from .reference_prompt_format import workflow_guidance
                function['description'] += workflow_guidance(workflow)
            if 'message_source' in function['parameters'].get('properties', {}):
                recovery_example = {'message_source': {'message_id': '00000000-0000-4000-8000-000000000001',
                    'content_index': 0, 'items': [{'title': '原方案标题', 'first_line': 1, 'last_line': 2}]},
                    'continue_execution': True}
                examples = function['parameters'].setdefault('examples', [])
                if recovery_example not in examples:
                    examples.append(recovery_example)
                function['description'] += (' 历史文字恢复的完整JSON结构示例：' + json.dumps(recovery_example, ensure_ascii=False)
                    + '。先读取用户指定的原消息，复制真实message_id、content_index和行范围；示例ID与行号不能当实际来源。'
                    'message_source是文字来源，不使用image_ref；已经登记的稿件使用content_ref读取和修订。')
            if 'examples' in function['parameters']:
                from services.tools.argument_validation import _issues
                function['parameters']['examples'] = [self.contents.example(v) for v in function['parameters']['examples']]
                function['parameters']['examples'] = [v for v in function['parameters']['examples']
                    if not _issues(v, function['parameters'], image_hints=False)]
            from .content_update_wire import project as flat_updates
            result['function'] = function = flat_updates(function)
            if profile == 'reference-prompts-v1' or (function['parameters'].get('properties', {}).get('document', {}).get('properties', {}).get('reference_analysis')):
                function['description'] += ' 完整JSON案例（示例不代表本次目标，引用须按真实目录选择）：' + json.dumps(
                    function['parameters']['examples'], ensure_ascii=False)
        if isinstance(function, dict) and function.get('name') in {'save_task_text', 'generate_image', 'generate_video', 'read_conversation_text', 'resolve_request_target', 'code_execute'}:
            function['description'] = function.get('description', '') + (
                ' 稿件引用统一使用当前目录content_ref及原item_index，后台还原真实稿件ID与版本；'
                '图片仍使用image_ref，两种引用不混用。')
            # Any JSON examples embedded in descriptions use the same projection as tool receipts.
            from .content_objects import revision_examples
            for reference in (True, False):
                native_examples = revision_examples(reference)
                from .reference_prompt_format import encode_example
                from .content_update_wire import flat_examples
                visible = [flat_examples(self.contents.example(self.encode(encode_example(v) if reference else v))) for v in native_examples]
                replacement = json.dumps(
                    function['parameters'].get('examples', visible) if function.get('name') == 'save_task_text' else visible,
                    ensure_ascii=False)
                for examples in (native_examples, flat_examples(native_examples)):
                    function['description'] = function['description'].replace(
                        json.dumps(examples, ensure_ascii=False), replacement)
            from .action_target_contract import selection_examples
            function['description'] = function['description'].replace(
                json.dumps(selection_examples(), ensure_ascii=False), json.dumps(
                    self.contents.example(selection_examples()), ensure_ascii=False))
            function['description'] = function['description'].replace('artifact_id', 'content_ref')
            from .content_update_wire import description as update_description
            function['description'] = update_description(function['description'])
        if isinstance(function, dict) and function.get('name') in {'generate_image', 'resolve_request_target', 'read_conversation_text'}:
            function['description'] = function.get('description', '') + ' 图片选择使用当前目录image_ref，后台解析真实图片身份，不填写图片消息ID和位置。'
        message_handle = next((handle for handle, ref in self.references.items() if 'message_id' in ref), None)
        if self.ready_image_refs is not None:
            message_handle = self.example_image_ref
        if isinstance(function, dict) and function.get('name') == 'read_conversation_text' and message_handle:
            if 'show_image' in function['parameters'].get('properties', {}):
                example = {'show_image': {'image_ref': message_handle}}
                function['description'] += (' 已有图片展示参数示例（从目录选择用户指定图，不默认选示例）：'
                    + json.dumps(example, ensure_ascii=False)
                    + '。view_image同样填写对象，但只供模型分析，不能代替向用户展示。')
            elif 'view_image' in function['parameters'].get('properties', {}):
                example = {'view_image': {'image_ref': message_handle}}
                function['description'] += (' 当前可用核图参数示例（只说明字段，不替用户选图）：'
                    + json.dumps(example, ensure_ascii=False)
                    + '。核图不等于展示，需展示时核对后单独选择目标。')
        selection_handle = next((handle for handle in self.references if 'message_id' in self.references[handle]
            and (self.ready_image_refs is None or handle in self.ready_image_refs)), None)
        if self.ready_image_refs is not None:
            selection_handle = self.example_image_ref if self.example_image_ref in self.ready_image_refs else None
        if isinstance(function, dict) and function.get('name') == 'resolve_request_target' and selection_handle:
            from .image_history import selection_arguments
            purposes = ([self.selection_action] if self.selection_action else
                [('edit', None), ('explain', 'display_existing'), ('explain', 'analyze'), ('write', None)])
            image_examples = [self.encode(selection_arguments(self.references[selection_handle], intent=intent, image_action=action))
                              for intent, action in purposes if intent in {'edit', 'explain', 'generate', 'write', 'write_then_submit_images'}]
            function['parameters'].setdefault('examples', []).extend(image_examples)
            function['description'] += (' 当前动作的完整图片目标JSON示例（只说明结构，不替用户选图）：'
                + json.dumps(image_examples, ensure_ascii=False)
                + '。image_ref必须按用户实际指代选择，不能抄示例。image_action=analyze只分析，不回显；'
                'display_existing才展示；引用图片写新方案仍是write，不能因有图片改成edit。')
        example_image = self.example_image_ref if self.ready_image_refs is not None else next(iter(self.references), None)
        if (isinstance(function, dict) and function.get('name') == 'generate_image' and example_image
                and 'edit_instruction' in function['parameters'].get('properties', {})):
            example = {'image_ref': example_image, 'edit_instruction': '本次修改和需要保留的内容'}
            function['description'] += (' 单图编辑格式示例（不代表替用户选图）：' + json.dumps(example, ensure_ascii=False)
                + '。底图只填image_ref，不复制目录中的name、task_id等元数据。'
                'materials只用于其他参考图，每项仅填写reference、role、usage。')
            if self.ready_image_refs is None:
                function['description'] += (' 无其他参考图时省略materials，不重复填写底图。其他参考图的完整结构示例：'
                    + json.dumps({'materials': [{'reference': {'image_ref': next(iter(self.references))},
                        'role': '其他图片的颜色参考', 'usage': 'input'}]}, ensure_ascii=False)
                    + '。示例仅说明嵌套结构，实际参考图须按用户要求选择，不能照抄示例编号。')
        if isinstance(function, dict) and function.get('name') == 'resolve_request_target' and self.ready_image_refs is not None:
            from services.tools.argument_validation import _issues
            parameters = function['parameters']
            definition = parameters.get('$defs', {}).get('Target', {})
            ready = [handle for handle in self.references if handle in self.ready_image_refs
                     and 'message_id' in self.references[handle]]
            for branch in definition.get('oneOf', ()):
                if branch.get('properties', {}).get('kind', {}).get('const') == 'image':
                    branch['properties']['image_ref'] = {'enum': ready} if ready else False
            parameters['examples'] = [example for example in parameters.get('examples', ())
                                     if not _issues(example, parameters, image_hints=False)]
            function['description'] += (' 图片目标只允许选择当前已满足前置条件的image_ref；'
                '其他历史候选先按其next_step核图，下一次提供的选择合同会更新。没有可选图不等于没有候选；'
                '不要为了绕过核图改成其他目标，也不默认选择示例。')
        props = result.get('properties', {})
        if {'body', 'title'} <= props.keys():
            props['title']['description'] = ('条目标题，必填title；不要使用heading等未定义字段。正文放body。'
                if 'title' in result.get('required', ()) else
                '仅修改条目标题时填写title；否则省略以保留原标题。不要使用heading。')
        if {'tool', 'wait_for', 'wait_all', 'approved', 'confirmation_only'} <= props.keys():
            props['tool'] = {'type': 'string', 'enum': ['generate_image', 'generate_video'],
                             'description': '这些等待/批准条件所针对的交付工具。无等待也明确填写，不能以null配合非空等待。'}
        if {'canvas_image', 'canvas_resource'} <= props.keys():
            props.pop('canvas_image'); props.pop('canvas_resource')
            canvases = [handle for handle, reference in self.references.items()
                        if 'resource_ref' in reference or reference in self.current_images]
            if canvases:
                props['canvas_ref'] = {'type': ['string', 'null'], 'enum': [*canvases, None],
                    'description': '如需显式指定当前上传/追加图作为画布，复制它的image_ref。历史编辑选target底图即可，不把历史图当本条附件。'}
        if {'source_message_id', 'source_content_index', 'edit_instruction'} <= props.keys():
            props.pop('source_message_id'); props.pop('source_content_index')
            props.pop('source_reference', None)
            result.pop('oneOf', None)
            props['image_ref'] = self.field()
            result['required'] = [k for k in result.get('required', ()) if k not in {'source_message_id', 'source_content_index'}] + ['image_ref']
        elif value.get('x-reference-kind') == 'image' and {'message_id', 'content_index'} <= props.keys():
            # MaterialReference supports other native file selectors as well.
            # The image locator form becomes a single snapshot-bound handle.
            props.pop('message_id'); props.pop('content_index')
            image_required = {'message_id', 'content_index'} <= set(value.get('required', ()))
            props['image_ref'] = self.field(message_only=image_required)
            result['required'] = [k for k in result.get('required', ()) if k not in {'message_id', 'content_index'}]
            if image_required:
                result['required'].append('image_ref')
            if not (set(props) - {'image_ref'}):
                result['required'] = ['image_ref']
            for branch in result.get('oneOf', ()):
                if 'message_id' in branch.get('required', ()):
                    branch['required'] = ['image_ref']
                elif branch.get('required') and 'image_ref' not in branch['required']:
                    branch.setdefault('properties', {})['image_ref'] = {'type': 'null'}
                for key in ('message_id', 'content_index'):
                    branch.get('properties', {}).pop(key, None)
        if props.get('kind', {}).get('enum') and 'image' in props['kind']['enum'] and 'basis' in props:
            props.pop('content_index', None)
            props['image_ref'] = self.field(message_only=True)
            props['source_message_id']['description'] = '仅用于request或content目标的真实来源消息ID；image目标使用image_ref，不填写此字段。'
            for branch in result.get('oneOf', ()):
                if branch.get('properties', {}).get('kind', {}).get('const') == 'image':
                    branch['required'] = ['kind', 'basis', 'image_ref']
                    branch['properties']['source_message_id'] = {'type': 'null'}
        if result.get('title') == 'SaveContent' and '$defs' in result:
            result = self.inline_definitions(result)
        result = self.contents.schema(result)
        if 'examples' in result:
            result['examples'] = [self.contents.example(self.encode(v)) for v in result['examples']]
        return result

    @staticmethod
    def inline_definitions(schema):
        """Put save fields at their use site; retain the same JSON constraints."""
        definitions = schema.get('$defs', {})
        def expand(value, trail=()):
            if isinstance(value, list):
                return [expand(item, trail) for item in value]
            if not isinstance(value, dict):
                return value
            reference = value.get('$ref')
            if isinstance(reference, str) and reference.startswith('#/$defs/'):
                name = reference.removeprefix('#/$defs/')
                if name in trail or name not in definitions:
                    raise TaskError('IMAGE_SAVE_SCHEMA_REFERENCE_INVALID')
                target = expand(definitions[name], (*trail, name))
                siblings = expand({key: item for key, item in value.items() if key != '$ref'}, trail)
                # JSON Schema $ref siblings are conjunctive, never overrides.
                if set(siblings) <= {'description', 'title', 'default', 'examples'}:
                    return {**target, **siblings}
                return {'allOf': [target, siblings]} if siblings else target
            return {key: expand(item, trail) for key, item in value.items() if key != '$defs'}
        return expand(schema)

    def save_repair(self, result, offered):
        """Describe the rejected form, without steering history recovery to new work."""
        if offered:
            result['parameters'] = self.schema({'function': offered})['function']['parameters']
        if self.reference_format:
            for issue in result.get('issues', ()):
                if issue.get('loc'):
                    loc = list(issue['loc'])
                    loc = [('reference_analysis' if i == 1 and loc[0] == 'document' else 'presentation')
                           if part == 'display_prefix' else 'prompt' if part == 'body' else part
                           for i, part in enumerate(loc)]
                    issue['loc'] = loc
        roots = {str(issue['loc'][0]) for issue in result.get('issues', ()) if issue.get('loc')}
        if offered:
            roots &= result['parameters'].get('properties', {}).keys()
        required = {}
        if 'document' in roots:
            required.update({'document': ['title', 'items'], 'document.items[]': ['title', 'body'],
                             'document.items[].materials[]': ['reference', 'role', 'usage']})
            result['message'] += ' document.items每项填写title及body，素材填写reference:{image_ref}、role、usage。'
        if 'message_source' in roots:
            required.update({'message_source': ['message_id'],
                             'message_source.items[]': ['title', 'first_line', 'last_line']})
            result['message'] += ' message_source须引用已读取原消息的行范围first_line和last_line，不重新编写原文。'
        if 'confirmation' in roots:
            result['message'] += ' confirmation须为JSON对象，按用户要求填写tool及wait_for或wait_all。'
        if roots & {'base', 'updates'}:
            from .content_objects import revision_usage, revision_examples
            reference = bool(self.reference_format or (offered and
                offered.get('parameters', {}).get('x-content-profile') == 'reference-prompts-v1'))
            required.update({'base': ['content_ref', 'item_index'],
                             'updates[]': ['item_index', 'item']})
            usage = revision_usage(reference)
            native = revision_examples(reference)
            usage = usage.replace(json.dumps(native, ensure_ascii=False), json.dumps(
                [self.contents.example(v) for v in native], ensure_ascii=False)).replace('artifact_id', 'content_ref')
            result['message'] += usage
        if required:
            result['required_fields'] = required
        from .reference_prompt_format import MODEL_GUIDANCE, save_recovery_fields
        if self.reference_format or (offered and offered.get('parameters', {}).get('x-content-profile') == 'reference-prompts-v1'):
            result['message'] = MODEL_GUIDANCE
        if offered:
            required, forms = save_recovery_fields(result['parameters'], roots)
            result['required_fields'] = required
            result['allowed_forms'] = forms
        result['message'] += ' 仅按所附parameters修复出错字段，保留原操作形式、全部正文、素材用途及规格。'
        from .content_update_wire import public_error
        return public_error(result)

    def tools(self, tools, *, plain_text_registration=False):
        # Pure fresh text already has a frozen, durable host-registration path.
        # Do not offer a second authoring protocol for that same delivery.
        return self.schema([tool for tool in tools if not (plain_text_registration
            and tool.get('function', {}).get('name') == 'save_task_text')])

    def selection_repair(self, rejection, arguments, parameters):
        """Explain the offered form; never infer or repair a user's selection."""
        from services.tools.argument_validation import _issues
        public = self.schema(parameters)
        frame = arguments.get('request_context')
        target = frame.get('target') if isinstance(frame, dict) else None
        if isinstance(target, dict):
            definition = public['$defs']['Target']
            branch = next((b for b in definition['oneOf']
                           if b['properties']['kind']['const'] == target.get('kind')), None)
            if branch:
                selected = {**definition, 'required': branch['required'],
                            'properties': {**definition['properties'], **branch['properties']}}
                selected.pop('oneOf')
                details = _issues(target, selected, image_hints=False)
                if details:
                    rejection['issues'] = [{'path': '$.request_context.target' + issue['path'][1:],
                                             'reason': issue['reason']} for issue in details]
        guidance = ('content目标从真实稿件目录复制content_ref、revision、item_indices及来源source_message_id；'
                    '执行或重做保存稿时focus=selected_target。'
                    if isinstance(target, dict) and target.get('kind') == 'content' else
                    'image目标填写kind、basis和单个image_ref；basis从枚举复制，不使用image_refs或item_indices代替图片引用。'
                    if isinstance(target, dict) and target.get('kind') == 'image' else
                    '按所选target.kind对应的字段定义填写，不混用图片引用和稿件引用。')
        rejection.update(parameters=public,
            message='本次未执行。按issues指出的字段修正，保留用户指定的目标与要求。'
                    + guidance + '不要重新编写或丢弃原稿。')
        return rejection

    def messages(self, messages):
        result = copy.deepcopy(messages)
        for message in result:
            message.pop('_visual_input_sources', None)
            message.pop('_host_context_kind', None)
            for part in message.get('content', ()) if isinstance(message.get('content'), list) else ():
                part.pop('_visual_reference', None)
            metadata = message.pop('_image_locator_projection', None)
            if metadata:
                from services.handlers.image_size_requirements import SIZE_GUIDANCE
                short = ('对话图片目录（来源事实，不代表已选中）。实际用图复制image_ref；'
                    '轮次和来源用于理解用户指代，不填写图片消息ID或位置。'
                    + SIZE_GUIDANCE + '\n' + json.dumps(self.encode(metadata['sources']), ensure_ascii=False))
                parts = message['content'] if isinstance(message.get('content'), list) else [message]
                for part in parts:
                    key = 'text' if part is not message else 'content'
                    text = part.get(key)
                    if isinstance(text, str) and text.endswith(metadata['text']):
                        part[key] = text[:-len(metadata['text'])] + short
            for call in message.get('tool_calls', ()):
                function = call.get('function', {})
                if isinstance(function.get('arguments'), str):
                    if function.get('name') == 'save_task_text':
                        try:
                            arguments = json.loads(function['arguments'])
                            base = arguments.get('base') or {}
                            if (call.get('id') in self.contents.authored_calls
                                    or self.contents.profiles.get(base.get('artifact_id')) == 'reference-prompts-v1'):
                                from .reference_prompt_format import encode_example
                                from .content_update_wire import flat_examples
                                function['arguments'] = json.dumps(flat_examples(encode_example(arguments)), ensure_ascii=False)
                        except (ValueError, TypeError, AttributeError, KeyError):
                            pass  # Failed/unknown historical forms remain evidence, never repaired by guessing.
                    function['arguments'] = self.text(function['arguments'])
            if message.get('role') in {'system', 'tool'} and isinstance(message.get('content'), str):
                message['content'] = self.text(message['content'])
            elif message.get('role') in {'system', 'tool'} and isinstance(message.get('content'), list):
                for part in message['content']:
                    if isinstance(part, dict) and part.get('type') in {'text', 'input_text'} and isinstance(part.get('text'), str):
                        part['text'] = self.text(part['text'])
        from .reference_boundary import REFERENCE_GUIDANCE
        result.append({'role': 'system', 'content': REFERENCE_GUIDANCE +
            '图片选择统一复制当前目录的image_ref；编辑用image_ref和edit_instruction，'
            '素材项的reference必须是对象{"image_ref":"当前目录编号"}，不能直接填编号字符串；'
            'view_image、show_image填写对象中的image_ref。不要填写图片消息ID和位置。'
            '后台还原真实身份并核验来源、权限和版本；短引用不代表执行授权。'
            '短引用仅用于工具参数，不向用户展示内部编号；用户看旧图时直接交付图片。'
            '稿件从当前目录或读取回执复制content_ref和原条目编号，不能填写artifact_id；'
            'content_ref只定位稿件，image_ref只定位图片，后台保留真实ID、版本、轮次和权限校验。'})
        return result

    def text(self, text):
        # Host facts are JSON, sometimes preceded by a system instruction line.
        # User prose and authored prompt bodies are never rewritten.
        prefix, sep, tail = text.rpartition('\n')
        # Legacy tool messages append one or more platform fact blocks to the
        # original result. Project each known block before recombining; never
        # treat arbitrary authored lines as JSON fragments to rewrite.
        if sep:
            try:
                metadata = json.loads(tail)
            except (ValueError, TypeError):
                metadata = None
            if (isinstance(metadata, dict) and set(metadata) == {'platform_metadata'}
                    and isinstance(metadata['platform_metadata'], dict)
                    and metadata['platform_metadata'].get('source') == 'platform_tool_context'):
                return self.text(prefix) + sep + json.dumps(metadata, ensure_ascii=False)
        for candidate, head in ((text, ''), (tail, prefix + sep)):
            try:
                parsed = json.loads(candidate)
            except (ValueError, TypeError):
                continue
            projected = self.encode(parsed)
            if isinstance(projected, dict):
                if projected.get('code') == 'CONTENT_PRESENTATION_INCOMPLETE':
                    from .reference_prompt_format import MODEL_GUIDANCE
                    projected['message'] = MODEL_GUIDANCE
                    for issue in projected.get('issues', ()):
                        issue['path'] = issue['path'].replace('$.document.display_prefix', '$.document.reference_analysis').replace(
                            '.display_prefix', '.presentation').replace('.body', '.prompt')
                if isinstance(projected.get('message'), str):
                    from .content_objects import MATERIAL_REFERENCE_GUIDANCE
                    if self.reference_format:
                        from .content_objects import IMAGE_REVISION_GUIDANCE
                        projected['message'] = projected['message'].replace(IMAGE_REVISION_GUIDANCE,
                            IMAGE_REVISION_GUIDANCE.replace('display_prefix', 'presentation').replace(
                                'body', 'prompt').replace('artifact_id', 'content_ref'))
                    projected['message'] = projected['message'].replace(MATERIAL_REFERENCE_GUIDANCE,
                        '图片素材填写reference:{"image_ref":"当前目录编号"}，reference不是字符串；保留role和usage。')
                    projected['message'] = projected['message'].replace(
                        '编辑图片统一填写 source_message_id、source_content_index 和 edit_instruction；',
                        '编辑图片统一填写image_ref和edit_instruction；')
                allowed = projected.get('allowed_fields')
                if isinstance(allowed, list) and {'source_message_id', 'source_content_index', 'edit_instruction'} <= set(allowed):
                    projected['allowed_fields'] = [key for key in allowed if key not in {'source_message_id', 'source_content_index'}] + ['image_ref']
                    for issue in projected.get('issues', ()):
                        if issue.get('path') in {'$.source_message_id', '$.source_content_index'}:
                            issue['path'] = '$.image_ref'
                if isinstance(projected.get('tools'), list):
                    projected['tools'] = self.schema(projected['tools'])
                if isinstance(projected.get('parameters'), dict):
                    projected['parameters'] = self.schema(projected['parameters'])
            return head + json.dumps(projected, ensure_ascii=False)
        return text


def for_runtime(runtime):
    snapshot = (getattr(runtime, 'checkpoint_base', {}) or {}).get('_request_context_v1') or {}
    if snapshot.get('image_reference_version') == 1:
        additions = [{'event_id': str(change.event_id), 'message_sha256': change.message_sha256,
                      'reference': {'resource_ref': attachment.resource_ref},
                      'name': attachment.workspace_path.rsplit('/', 1)[-1]}
                     for change in getattr(getattr(runtime, 'task_user_changes', None), 'items', ())
                     if change.delivered and str(change.owner_task_id) == str(runtime.task_id)
                     for attachment in change.attachments if attachment.kind == 'image']
        revisions = [(str(change.event_id), change.message_sha256)
                     for change in getattr(getattr(runtime, 'task_user_changes', None), 'items', ())
                     if change.delivered and str(change.owner_task_id) == str(runtime.task_id)]
        catalog = ImageReferences({**snapshot, **({'_delivered_image_changes': additions} if additions else {}),
                                   **({'_delivered_input_revisions': revisions} if revisions else {})})
        from .request_resolution import groups
        for group in groups(runtime):
            catalog.contents.add(group)
        from .content_revision import selected_format
        from .direct_loop import active_image_methods
        selected = selected_format(runtime)
        catalog.reference_format = (selected.get('profile_id') == 'reference-prompts-v1' if selected else
            any(method[0] == 'reference-prompts-v1' for method in active_image_methods(runtime).values()))
        # Old immutable versions can be named in history, but never become selectable again.
        from .content_objects import objects, ContentRecord
        from .contracts import TaskState
        state = getattr(runtime, 'task_execution_state', None)
        candidate = getattr(runtime, 'task_followup_candidate', None)
        if state is None and candidate and candidate.get('state'):
            state = TaskState.model_validate(candidate['state'])
        for artifact in objects(state):
            record = ContentRecord.model_validate_json(artifact.inline_text)
            catalog.contents.profiles[artifact.id] = record.presentation_profile
            if record.presentation_profile == 'reference-prompts-v1':
                catalog.contents.authored_calls.add(artifact.source_call_id)
                catalog.contents.authored_calls.add(artifact.source_call_id.removesuffix(':continued'))
            if artifact.id not in catalog.contents.reverse:
                catalog.contents.add({'artifact_id': artifact.id, 'object_key': record.object_key,
                    'items': [{'item_index': index, 'title': item.title} for index, item in enumerate(record.document.items, 1)]}, current=False)
        from .image_history import recovered
        for image in recovered(runtime):
            handle = catalog.add(image['reference'])
            if not any(i['image_ref'] == handle for i in catalog.candidates):
                catalog.candidates.append({'image_ref': handle, 'name': image.get('name'),
                    'turn_number': image.get('source_turn'), 'provenance': image['provenance'],
                    'context_revision': image['context_revision'], 'retrieved_from_history': True})
        from .image_history import requires_view, selection_metadata, selection_purpose
        catalog.selection_action = selection_purpose(runtime)
        from .request_resolution import binding
        selected = binding(runtime) or {}
        target = selected.get('target') or {}
        if target.get('kind') != 'image':
            target = (selected.get('rejection') or {}).get('candidate_target') or {}
        if (target.get('kind') == 'image' and isinstance(target.get('source_message_id'), str)
                and type(target.get('content_index')) is int):
            catalog.example_image_ref = catalog.reverse.get(catalog.identity({
                'message_id': target['source_message_id'], 'content_index': target['content_index']}))
        catalog.ready_image_refs = {handle for handle, ref in catalog.references.items()
                                   if not requires_view(runtime, ref, model_view=True)}
        for candidate in catalog.candidates:
            ref = catalog.references[candidate['image_ref']]
            if 'message_id' in ref:
                state = {'reference': ref}
                selection_metadata(runtime, [state], model_view=True)
                candidate.update(catalog.encode({key: val for key, val in state.items() if key != 'reference'}))
        return catalog
    return None
