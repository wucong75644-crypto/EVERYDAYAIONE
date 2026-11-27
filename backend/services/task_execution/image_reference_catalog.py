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
            self.candidates.append({'image_ref': handle, 'name': image.get('name'),
                'turn_number': image.get('generated_for_turn'), 'provenance': 'generated_result'})
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

    def field(self):
        return {'type': 'string', **({'enum': list(self.references)} if self.references else {}),
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
        locator_fields = {'message_id', 'content_index', 'source_message_id', 'source_content_index',
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
            if 'message_id' not in reference:
                raise TaskError('IMAGE_REFERENCE_REQUIRES_DRAFT')
            result.update(source_message_id=reference['message_id'], source_content_index=reference['content_index'])
        else:
            result.update(reference)
        return result

    def decode_call(self, name, value):
        result = self.decode(value)
        if name == 'save_task_text':
            from .reference_prompt_format import decode_save
            result = decode_save(result, lambda identity, index: self.contents.items.get((identity, index)))
        return result

    def encode(self, value):
        if isinstance(value, list):
            return [self.encode(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: self.encode(item) for key, item in value.items()}
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
        result = {key: self.schema(item) for key, item in value.items()}
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
            if 'document' in function['parameters'].get('properties', {}):
                from .content_objects import creation_examples
                document_schema = function['parameters']['properties']['document']
                reference = 'display_prefix' in document_schema.get('required', ())
                example = creation_examples(reference)[0]
                function['description'] += (' 新建参数的对象结构示例（内容须按实际要求填写，素材另按目录绑定）：'
                    + json.dumps(example, ensure_ascii=False)
                    + '。items每项的标题字段固定为title，执行正文固定为body。'
                    '如用户要求等待确认，confirmation是JSON对象，例如'
                    '{"tool":"generate_image","wait_for":[1]}，不是序列化后的字符串；等待条目按实际要求填写。')
            from .reference_prompt_format import project_tool, MODEL_GUIDANCE
            result['function'] = function = project_tool(function)
            if 'examples' in function['parameters']:
                function['parameters']['examples'] = [self.contents.example(v) for v in function['parameters']['examples']]
            if function.get('description', '').startswith(MODEL_GUIDANCE):
                function['description'] = MODEL_GUIDANCE + ' 完整JSON案例（示例不代表本次目标，引用须按真实目录选择）：' + json.dumps(
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
                visible = [self.contents.example(self.encode(encode_example(v) if reference else v)) for v in native_examples]
                function['description'] = function['description'].replace(
                    json.dumps(native_examples, ensure_ascii=False), json.dumps(visible, ensure_ascii=False))
            from .action_target_contract import selection_examples
            function['description'] = function['description'].replace(
                json.dumps(selection_examples(), ensure_ascii=False), json.dumps(
                    self.contents.example(selection_examples()), ensure_ascii=False))
            function['description'] = function['description'].replace('artifact_id', 'content_ref')
        if isinstance(function, dict) and function.get('name') in {'generate_image', 'resolve_request_target', 'read_conversation_text'}:
            function['description'] = function.get('description', '') + ' 图片选择使用当前目录image_ref，后台解析真实图片身份，不填写图片消息ID和位置。'
        if isinstance(function, dict) and function.get('name') == 'read_conversation_text' and self.references:
            if 'show_image' in function['parameters'].get('properties', {}):
                example = {'show_image': {'image_ref': next(iter(self.references))}}
                function['description'] += (' 已有图片展示参数示例（从目录选择用户指定图，不默认选示例）：'
                    + json.dumps(example, ensure_ascii=False)
                    + '。view_image同样填写对象，但只供模型分析，不能代替向用户展示。')
            elif 'view_image' in function['parameters'].get('properties', {}):
                example = {'view_image': {'image_ref': next(iter(self.references))}}
                function['description'] += (' 当前可用核图参数示例（只说明字段，不替用户选图）：'
                    + json.dumps(example, ensure_ascii=False)
                    + '。核图不等于展示，需展示时核对后单独选择目标。')
        if isinstance(function, dict) and function.get('name') == 'resolve_request_target' and self.references:
            example = {'intent': 'edit', 'request_context': {'focus': 'selected_target',
                'target': {'kind': 'image', 'basis': 'explicit_reference', 'image_ref': next(iter(self.references))}}}
            function['description'] += (' 完整图片目标格式示例（仅说明字段，不代表替用户选中示例图片；'
                'image_ref须按用户实际指代从当前目录复制）：' + json.dumps(example, ensure_ascii=False)
                + '。image_ref是单个字符串，不是image_refs数组；basis只使用定义中的枚举。'
                '当前目标已经正确绑定时直接执行，不重复纠正目标。')
            readonly_examples = []
            for action in ('display_existing', 'analyze'):
                readonly = copy.deepcopy(example)
                readonly['intent'] = 'explain'
                readonly['request_context']['image_action'] = action
                readonly_examples.append(readonly)
            function['parameters'].setdefault('examples', []).extend(readonly_examples)
            function['description'] += (' 展示旧图和仅分析的完整JSON分别为：'
                + json.dumps(readonly_examples, ensure_ascii=False)
                + '。示例图片编号仅说明结构，不是本次选择；实际复制用户指定候选的image_ref。'
                  '展示必须先绑定具体目标，查询本身不完成选择；analyze只分析，不回显。')
            new_plan = copy.deepcopy(example)
            new_plan['intent'] = 'write'
            new_plan['request_context']['focus'] = 'current_request'
            function['description'] += (' 引用图片写新方案的完整JSON（不是编辑成图）：'
                + json.dumps(new_plan, ensure_ascii=False) + '。实际动作按用户要求选择，不因存在图片就填写edit。')
        if (isinstance(function, dict) and function.get('name') == 'generate_image' and self.references
                and 'edit_instruction' in function['parameters'].get('properties', {})):
            example = {'image_ref': next(iter(self.references)), 'edit_instruction': '本次修改和需要保留的内容'}
            function['description'] += (' 单图编辑格式示例（不代表替用户选图）：' + json.dumps(example, ensure_ascii=False)
                + '。底图只填image_ref，不复制目录中的name、task_id等元数据。'
                'materials只用于其他参考图，每项仅填写reference、role、usage。')
            function['description'] += (' 无其他参考图时省略materials，不重复填写底图。其他参考图的完整结构示例：'
                + json.dumps({'materials': [{'reference': {'image_ref': next(iter(self.references))},
                    'role': '其他图片的颜色参考', 'usage': 'input'}]}, ensure_ascii=False)
                + '。示例仅说明嵌套结构，实际参考图须按用户要求选择，不能照抄示例编号。')
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
            props['image_ref'] = self.field()
            result['required'] = [k for k in result.get('required', ()) if k not in {'source_message_id', 'source_content_index'}] + ['image_ref']
        elif {'message_id', 'content_index'} <= props.keys():
            # MaterialReference supports other native file selectors as well.
            # The image locator form becomes a single snapshot-bound handle.
            props.pop('message_id'); props.pop('content_index')
            props['image_ref'] = self.field()
            image_required = {'message_id', 'content_index'} <= set(value.get('required', ()))
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
            props['image_ref'] = self.field()
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
        roots = {str(issue['loc'][0]) for issue in result.get('issues', ()) if issue.get('loc')}
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
            from .methods import REFERENCE_PROMPTS_PRESENTATION
            from .reference_prompt_format import MODEL_GUIDANCE
            reference = bool(offered and offered.get('description', '').startswith((REFERENCE_PROMPTS_PRESENTATION, MODEL_GUIDANCE)))
            required.update({'base': ['content_ref', 'item_index'],
                             'updates[]': ['item_index', 'item']})
            usage = revision_usage(reference)
            native = revision_examples(reference)
            usage = usage.replace(json.dumps(native, ensure_ascii=False), json.dumps(
                [self.contents.example(v) for v in native], ensure_ascii=False)).replace('artifact_id', 'content_ref')
            result['message'] += usage
        if required:
            result['required_fields'] = required
        from .reference_prompt_format import MODEL_GUIDANCE
        if offered and offered.get('description', '').startswith(('按当前参考图提示词方法', MODEL_GUIDANCE)):
            from .reference_prompt_format import MODEL_GUIDANCE
            result['message'] = MODEL_GUIDANCE
            result['required_fields'] = ({'document': ['title', 'reference_analysis', 'items'],
                'document.items[]': ['title', 'presentation', 'prompt']}
                if 'document' in roots else {'base': ['content_ref', 'item_index'], 'updates[]': ['item_index', 'item']})
        result['message'] += ' 仅按所附parameters修复出错字段，保留原操作形式、全部正文、素材用途及规格。'
        return result

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
                                function['arguments'] = json.dumps(encode_example(arguments), ensure_ascii=False)
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
        return catalog
    return None
