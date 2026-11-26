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
        self.scope = hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()[:10]
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

    def decode(self, value):
        if isinstance(value, list):
            return [self.decode(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: self.decode(item) for key, item in value.items() if key != 'image_ref'}
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

    def encode(self, value):
        if isinstance(value, list):
            return [self.encode(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: self.encode(item) for key, item in value.items()}
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
                '图片复制当前目录的image_ref；其他已核验文件可使用resource_ref、file_id或asset_id。')
            result['description'] = result['description'].replace(
                '从该图的 reference 原样复制 message_id 和 content_index 到对应字段。', '复制该图的image_ref。').replace(
                '编辑图片统一填写 source_message_id、source_content_index 和 edit_instruction；',
                '编辑图片统一填写image_ref和edit_instruction；')
        function = result.get('function')
        if isinstance(function, dict) and function.get('name') == 'save_task_text':
            if 'document' in function['parameters'].get('properties', {}):
                example = {'document': {'title': '方案标题', 'items': [{'title': '条目标题', 'body': '完整执行正文'}]}}
                function['description'] += (' 新建参数的对象结构示例（内容须按实际要求填写，素材另按目录绑定）：'
                    + json.dumps(example, ensure_ascii=False)
                    + '。items每项的标题字段固定为title，执行正文固定为body。'
                    '如用户要求等待确认，confirmation是JSON对象，例如'
                    '{"tool":"generate_image","wait_for":[1]}，不是序列化后的字符串；等待条目按实际要求填写。')
        if isinstance(function, dict) and function.get('name') in {'generate_image', 'resolve_request_target', 'read_conversation_text'}:
            function['description'] = function.get('description', '') + ' 图片选择使用当前目录image_ref，后台解析真实图片身份，不填写图片消息ID和位置。'
        props = result.get('properties', {})
        if {'body', 'title'} <= props.keys():
            props['title']['description'] = '条目标题，必填title；不要使用heading等未定义字段。正文放body。'
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
            for branch in result.get('oneOf', ()):
                if branch.get('properties', {}).get('kind', {}).get('const') == 'image':
                    branch['required'] = ['kind', 'basis', 'image_ref']
                    branch['properties']['source_message_id'] = {'type': 'null'}
        if result.get('title') == 'SaveContent' and '$defs' in result:
            result = self.inline_definitions(result)
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
            result['parameters'] = self.schema(offered['parameters'])
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
        if required:
            result['required_fields'] = required
        result['message'] += ' 仅按所附parameters修复出错字段，保留原操作形式、全部正文、素材用途及规格。'
        return result

    def tools(self, tools, *, plain_text_registration=False):
        # Pure fresh text already has a frozen, durable host-registration path.
        # Do not offer a second authoring protocol for that same delivery.
        return self.schema([tool for tool in tools if not (plain_text_registration
            and tool.get('function', {}).get('name') == 'save_task_text')])

    def messages(self, messages):
        result = copy.deepcopy(messages)
        for message in result:
            for call in message.get('tool_calls', ()):
                function = call.get('function', {})
                if isinstance(function.get('arguments'), str):
                    function['arguments'] = self.text(function['arguments'])
            if message.get('role') in {'system', 'tool'} and isinstance(message.get('content'), str):
                message['content'] = self.text(message['content'])
        result.append({'role': 'system', 'content':
            '图片选择统一复制当前目录的image_ref；编辑用image_ref和edit_instruction，'
            '素材reference、view_image、show_image也填写image_ref。不要填写图片消息ID和位置。'
            '后台还原真实身份并核验来源、权限和版本；短引用不代表执行授权。'
            '短引用仅用于工具参数，不向用户展示内部编号；用户看旧图时直接交付图片。'})
        return result

    def text(self, text):
        # Host facts are JSON, sometimes preceded by a system instruction line.
        # User prose and authored prompt bodies are never rewritten.
        prefix, sep, tail = text.rpartition('\n')
        for candidate, head in ((text, ''), (tail, prefix + sep)):
            try:
                parsed = json.loads(candidate)
            except (ValueError, TypeError):
                continue
            projected = self.encode(parsed)
            if isinstance(projected, dict):
                if isinstance(projected.get('message'), str):
                    from .content_objects import MATERIAL_REFERENCE_GUIDANCE
                    projected['message'] = projected['message'].replace(MATERIAL_REFERENCE_GUIDANCE,
                        '图片素材的reference只填当前目录image_ref，并保留role和usage。')
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
        return ImageReferences({**snapshot, **({'_delivered_image_changes': additions} if additions else {}),
                                **({'_delivered_input_revisions': revisions} if revisions else {})})
    return None
