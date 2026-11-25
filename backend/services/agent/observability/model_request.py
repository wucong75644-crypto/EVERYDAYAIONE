"""Final serialized request diagnostics; never logs prompts or tool arguments."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json

_CAPTURE = ContextVar('model_wire_capture', default=None)
MAX_SCHEMA_BYTES = 64 * 1024


@contextmanager
def capture_requests(rows):
    token = _CAPTURE.set(rows)
    try:
        yield
    finally:
        _CAPTURE.reset(token)


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str).encode()


def record_request(body, protocol):
    """Called after provider-specific conversion, just before transport."""
    try:
        tools = body.get('tools') or []
        encoded = _encoded(tools)
        messages = body.get('messages', body.get('input', []))
        metrics = {'protocol': protocol, 'model': body.get('model'),
                   'function_tools': sum(t.get('type') == 'function' for t in tools),
                   'hosted_tools': [t.get('type') for t in tools if t.get('type') != 'function'],
                   'tool_bytes': len(encoded), 'tool_sha256': hashlib.sha256(encoded).hexdigest(),
                   'input_wire_bytes': len(_encoded(messages)),
                   'message_count': len(messages) if isinstance(messages, list) else 0,
                   'reasoning': body.get('reasoning'), 'enable_thinking': body.get('enable_thinking'),
                   'reasoning_effort': body.get('reasoning_effort'),
                   'max_output_tokens': body.get('max_output_tokens', body.get('max_tokens'))}
        from services.model_gateway import get_model_attempt_context
        attempt = get_model_attempt_context()
        if attempt is not None:
            metrics.update(task_id=attempt.task_id, request_id=attempt.request_id, attempt_id=attempt.attempt_id)
        if isinstance(messages, list):
            sizes = [len(_encoded(m)) for m in messages]
            metrics['role_bytes'] = {role: sum(size for m, size in zip(messages, sizes)
                                              if isinstance(m, dict) and m.get('role') == role)
                                     for role in ('system', 'developer', 'user', 'assistant', 'tool')}
            boundary = next((i for i in range(len(messages) - 1, -1, -1)
                             if isinstance(messages[i], dict) and messages[i].get('role') == 'user'), None)
            metrics['current_user_bytes'] = sizes[boundary] if boundary is not None else 0
            metrics['history_bytes'] = sum(size for i, (m, size) in enumerate(zip(messages, sizes))
                                           if boundary is not None and i < boundary and isinstance(m, dict)
                                           and m.get('role') not in ('system', 'developer'))
            metrics['tool_loop_bytes'] = sum(sizes[boundary + 1:]) if boundary is not None else 0
            def text_content(message):
                content = message.get('content') or ''
                if isinstance(content, str):
                    return content
                return ''.join(p.get('text', '') for p in content if isinstance(p, dict)) if isinstance(content, list) else ''
            prefixes = ('[Turn Skill instructions:', '[Current Skill task]', '[Skill suggestions', '[Available Skills')
            metrics['skill_message_bytes'] = sum(size for m, size in zip(messages, sizes)
                                                if isinstance(m, dict) and m.get('role') in ('system', 'developer')
                                                and text_content(m).startswith(prefixes))
            # Counts structured image parts, never expands/logs image URLs.
            def image_count(value):
                if isinstance(value, list):
                    return sum(image_count(v) for v in value)
                if isinstance(value, dict):
                    return int(value.get('type') in ('image_url', 'input_image')) + sum(
                        image_count(v) for k, v in value.items() if k != 'image_url')
                return 0
            metrics['image_parts'] = image_count(messages)
        rows = _CAPTURE.get()
        if rows is not None and len(rows) < 8:
            rows.append({**metrics, 'tools_schema': tools if len(encoded) <= MAX_SCHEMA_BYTES else None,
                         'schema_omitted': len(encoded) > MAX_SCHEMA_BYTES})
        from .chat_stream import publish_chat_stream_summary
        publish_chat_stream_summary({'scope': 'model_wire', **metrics})
    except Exception:
        # Measurement must not change transport, cancellation or billing behavior.
        pass
