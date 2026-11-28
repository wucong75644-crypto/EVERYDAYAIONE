"""Snapshot-bound draft handles. Native IDs, versions and source ownership never change."""
import hashlib

from .contracts import TaskError


class ContentReferenceError(TaskError):
    def __init__(self, code, path, allowed):
        self.path, self.allowed = path, allowed
        super().__init__(code)


class ContentReferences:
    def __init__(self, scope):
        self.scope = scope
        self.references, self.reverse, self.items = {}, {}, {}
        self.selectable = set()
        self.objects = {}
        self.revisions = {}
        self.profiles, self.authored_calls = {}, set()

    def add(self, group, *, current=True):
        from .reference_boundary import require_native
        require_native(group)
        identity = group['artifact_id']
        handle = 'doc_' + self.scope + '_' + hashlib.sha256(identity.encode()).hexdigest()[:12]
        if handle in self.references and self.references[handle] != identity:
            raise TaskError('CONTENT_REFERENCE_COLLISION')
        self.references[handle], self.reverse[identity] = identity, handle
        object_key, revision = group.get('object_key'), group.get('revision')
        peers = [ref for ref in self.selectable if object_key and self.objects.get(ref) == object_key]
        if current and type(revision) is int:
            newer = any(self.revisions.get(ref, 0) > revision for ref in peers)
            self.selectable.difference_update(ref for ref in peers if self.revisions.get(ref, 0) < revision)
            if not newer:
                self.selectable.add(handle)
        elif current:
            self.selectable.add(handle)
        self.objects[handle] = object_key
        if type(revision) is int:
            self.revisions[handle] = revision
        for item in group.get('items', ()):
            if item.get('title'):
                self.items[(identity, item['item_index'])] = item['title']

    def available(self):
        return [handle for handle in self.references if handle in self.selectable]

    def encode(self, value):
        result = dict(value)
        if isinstance(value.get('artifact_id'), str) and value['artifact_id'] in self.reverse:
            result['content_ref'] = self.reverse[value['artifact_id']]
            result.pop('artifact_id')
        if 'saved_groups' in value and isinstance(value.get('instruction'), str):
            result['instruction'] = value['instruction'].replace('artifact_id', 'content_ref')
        return result

    def example(self, value):
        if isinstance(value, list):
            return [self.example(v) for v in value]
        if not isinstance(value, dict):
            return value
        result = {k: self.example(v) for k, v in value.items()}
        if result.get('artifact_id') == 'content-example':
            result.pop('artifact_id')
            result['content_ref'] = next(iter(self.available()), 'doc_example')
        return self.encode(result)

    def decode(self, value, path):
        result = dict(value)
        if 'content_ref' not in result:
            return result
        handle = result.pop('content_ref')
        if handle is None and (value.get('kind') not in (None, 'content')
                or set(value) & {'tool', 'approved', 'wait_for', 'wait_all', 'confirmation_only'}):
            if result.get('artifact_id') is not None:
                raise ContentReferenceError('CONTENT_REFERENCE_IDENTITY_CONFLICT', path + '.content_ref', self.available())
            result['artifact_id'] = None
            return result
        if result.get('artifact_id') is not None:
            raise ContentReferenceError('CONTENT_REFERENCE_IDENTITY_CONFLICT', path + '.content_ref', self.available())
        if not isinstance(handle, str) or handle not in self.references:
            code = ('CONTENT_REFERENCE_EXPIRED' if isinstance(handle, str) and handle.startswith('doc_')
                    and not handle.startswith('doc_' + self.scope + '_') else 'CONTENT_REFERENCE_UNKNOWN')
            raise ContentReferenceError(code, path + '.content_ref', self.available())
        if handle not in self.selectable:
            peers = [ref for ref in self.available() if self.objects.get(ref) == self.objects.get(handle)]
            raise ContentReferenceError('CONTENT_REFERENCE_VERSION_CHANGED', path + '.content_ref', peers)
        result.pop('artifact_id', None)
        result['artifact_id'] = self.references[handle]
        return result

    def schema(self, schema):
        """Called on every schema node after child projection."""
        props = schema.get('properties', {})
        if 'artifact_id' in props and (set(props) & {'item_index', 'kind', 'approved'}):
            props.pop('artifact_id')
            field = {'type': 'string', **({'enum': self.available()} if self.available() else {}),
                     'description': '从当前稿件目录或读取回执复制content_ref；绑定真实稿件版本，不能猜号、抄示例或沿用其他输入的编号。'}
            if isinstance(schema.get('title'), str) and schema['title'] in {'Target', 'MediaDirective'}:
                field = {'anyOf': [field, {'type': 'null'}], 'default': None}
            props['content_ref'] = field
            schema['required'] = ['content_ref' if k == 'artifact_id' else k for k in schema.get('required', ())]
            for branch in schema.get('oneOf', ()):
                branch['required'] = ['content_ref' if k == 'artifact_id' else k for k in branch.get('required', ())]
        return schema

    def repair(self, error):
        reason = {'CONTENT_REFERENCE_IDENTITY_CONFLICT': 'content_ref不可与artifact_id混用',
                  'CONTENT_REFERENCE_UNKNOWN': '当前目录没有这个稿件引用',
                  'CONTENT_REFERENCE_EXPIRED': '该引用属于其他输入，请使用本次目录的引用',
                  'CONTENT_REFERENCE_VERSION_CHANGED': '该稿件已有新版本，先核实当前版本再读取和修改'}[str(error)]
        return {'ok': False, 'accepted': False, 'code': str(error), 'submission_state': 'not_dispatched',
                'issues': [{'path': error.path, 'reason': reason}],
                'recovery': {'action': 'repair_arguments', 'allowed_content_refs': error.allowed},
                'message': '未保存或派发。从当前稿件目录复制content_ref；保留用户选择、条目编号和全部修改，不自动选择其他稿件。'}
