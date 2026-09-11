"""Read-only, synthetic probes for the block-05 diagnosis; no external services.

This records existing behavior, not repaired behavior or model obedience.
Run from the task worktree with PYTHONPATH=backend and the documented test env.
"""
import json

from services.handlers.chat_context.history_loader import _row_to_oai_messages
from services.handlers.tool_loop_context import ToolLoopContext
from services.handlers.context_compressor.tokens import deduplicate_system_prompts
from services.tools.file_calls import validate_selectors
from services.file_resources import FileTargetError


def main():
    findings = {}
    for kind, block in {
        "form": {"type": "form", "form_id": "test-form", "status": "open"},
        "file": {"type": "file", "name": "synthetic.xlsx", "workspace_path": "generated/synthetic.xlsx"},
        "table": {"type": "table", "columns": ["value"], "rows": [{"value": 1}]},
    }.items():
        messages, _ = _row_to_oai_messages(
            {"role": "assistant", "content": [block]}, remaining_images=0,
        )
        assert messages == []
        findings[f"completed_{kind}_only_history_is_dropped"] = True

    messages, _ = _row_to_oai_messages(
        {"role": "assistant", "content": [{"type": "text", "text": "已提供表单"},
         {"type": "form", "form_id": "test-form", "status": "open"}]},
        remaining_images=0,
    )
    assert messages == [{"role": "assistant", "content": "已提供表单"}]
    findings["text_is_preserved_but_nontext_outcome_is_not_projected"] = True

    context = ToolLoopContext()
    context.update_from_result("file_analyze", "RESOURCE_REFERENCE_INVALID", True)
    context.update_from_result("file_analyze", "读取成功", False)
    prompt = context.build_context_prompt()
    assert "上轮失败工具: file_analyze" in prompt
    findings["failure_hint_survives_later_success"] = True
    loop_messages = [{"role": "system", "content": prompt}]
    deduplicate_system_prompts(loop_messages)
    loop_messages.append({"role": "system", "content": context.build_context_prompt()})
    assert len(loop_messages) == 2
    findings["dedup_before_append_can_leave_two_failure_hints"] = True

    validate_selectors({"file_id": "fid_12345678"})
    findings["valid_fid_only_passes_selector_syntax"] = True
    try:
        validate_selectors({"file_id": "fid_12345678", "resource_ref": "synthetic.xlsx"})
    except FileTargetError as error:
        assert error.code == "RESOURCE_REFERENCE_INVALID"
        findings["valid_fid_does_not_override_invalid_extra_reference"] = True
    else:
        raise AssertionError("Expected invalid resource_ref rejection")
    print(json.dumps({"purpose": "existing-behavior diagnosis, not acceptance", "observations": findings},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
