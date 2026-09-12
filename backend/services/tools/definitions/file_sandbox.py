"""file_sandbox ToolSpec ownership. Business handlers are unchanged."""

from ..spec import Exposure, ToolAvailability, ToolPolicyRules, ToolSpec
from . import file_schemas
from . import code_schemas


def build_specs():
    schemas = {}
    schemas.update((s["function"]["name"], s) for s in file_schemas.build_file_tools())
    schemas.update((s["function"]["name"], s) for s in code_schemas.build_code_tools())
    return (
        ToolSpec(
            name='file_search', schema=schemas['file_search'],
            domain='general', availability=ToolAvailability(feature_flags=('file_workspace_enabled',)),
            risk_level='safe', parallelizable=True, cacheable=True,
            effects=('file_index',), executor_type="legacy", handler_key='file_search',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.file_sandbox.build_specs", definition_kind="explicit",
            catalog_order=19, catalog_groups=('file_tools',), core=True, legacy_plan_visible=True,
            compatibility_notes=('scope defaults to current without a bound browse; explicit scope remains a narrowing selection, never authorization',),
            legacy_validation_schema=file_schemas.FILE_TOOL_SCHEMAS.get('file_search'),
            policy_rules=ToolPolicyRules(
                operation='read',
                plan_allowed=True,
                execution_modes=('interactive', 'scheduled', 'preflight'),
                action_rule=None,
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='file_analyze', schema=schemas['file_analyze'],
            domain='general', availability=ToolAvailability(feature_flags=('file_workspace_enabled',)),
            risk_level='safe', parallelizable=True, cacheable=True,
            effects=('workspace_artifacts', 'file_index'), executor_type="legacy", handler_key='file_analyze',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.file_sandbox.build_specs", definition_kind="explicit",
            catalog_order=20, catalog_groups=('file_tools',), core=True, legacy_plan_visible=True,
            compatibility_notes=('file_id preferred; legacy path and scope retained',),
            legacy_validation_schema=file_schemas.FILE_TOOL_SCHEMAS.get('file_analyze'),
            policy_rules=ToolPolicyRules(
                operation='analysis',
                plan_allowed=True,
                execution_modes=('interactive', 'scheduled', 'preflight'),
                action_rule=None,
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='file_delete', schema=schemas['file_delete'],
            domain='general', availability=ToolAvailability(feature_flags=('file_workspace_enabled',)),
            risk_level='dangerous', parallelizable=False, cacheable=False,
            effects=('workspace_delete', 'deletion_record'), executor_type="legacy", handler_key='file_delete',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.file_sandbox.build_specs", definition_kind="explicit",
            catalog_order=21, catalog_groups=('file_tools',), core=True, legacy_plan_visible=True,
            compatibility_notes=('file_ids and legacy files retained; handler accepts string or list', 'when both are given, resolved file_ids are appended to files; do not replace this behavior'),
            legacy_validation_schema=file_schemas.FILE_TOOL_SCHEMAS.get('file_delete'),
            policy_rules=ToolPolicyRules(
                operation='business_write',
                plan_allowed=False,
                execution_modes=('interactive', 'scheduled'),
                action_rule=None,
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='restore_file', schema=schemas['restore_file'],
            domain='general', availability=ToolAvailability(feature_flags=('file_workspace_enabled',)),
            risk_level='safe', parallelizable=False, cacheable=False,
            effects=('workspace_write', 'deletion_record'), executor_type="legacy", handler_key='restore_file',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.file_sandbox.build_specs", definition_kind="explicit",
            catalog_order=22, catalog_groups=('file_tools',), core=True, legacy_plan_visible=True,
            compatibility_notes=('legacy safe risk and serial scheduling retained despite workspace writes',),
            legacy_validation_schema=file_schemas.FILE_TOOL_SCHEMAS.get('restore_file'),
            policy_rules=ToolPolicyRules(
                operation='business_write',
                plan_allowed=False,
                execution_modes=('interactive', 'scheduled'),
                action_rule=None,
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='code_execute', schema=schemas['code_execute'],
            domain='shared', availability=ToolAvailability(feature_flags=('sandbox_enabled',)),
            risk_level='confirm', parallelizable=True, cacheable=True,
            effects=('kernel_state', 'workspace_artifacts'), executor_type="legacy", handler_key='code_execute',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.file_sandbox.build_specs", definition_kind="explicit",
            catalog_order=23, catalog_groups=('code_tools',), core=True, legacy_plan_visible=True,
            compatibility_notes=('legacy cache eligibility retained independently; kernel state is not read-only',),
            legacy_validation_schema=code_schemas.CODE_TOOL_SCHEMAS.get('code_execute'),
            policy_rules=ToolPolicyRules(
                operation='analysis',
                plan_allowed=True,
                execution_modes=('interactive', 'scheduled', 'preflight'),
                action_rule=None,
                required_permissions=(),
            ),
        ),
    )
