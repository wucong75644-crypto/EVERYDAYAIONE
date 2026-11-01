"""Verify an existing native read export; never create a file from model text."""
from pathlib import Path

from .contracts import TaskError
from .reducer import digest


def verify_file(state,extension,context):
    from core.config import get_settings
    from services.file_executor import FileExecutor
    from services.file_resources import content_digest
    from services.tools.resource_access import ResourceAccessBoundary
    from services.tools.result_payload import decode_raw
    from services.tools.spec import thaw
    try:
        if (context is None or context.actor_user_id!=str(state.actor_user_id)
            or context.workspace_owner_id!=state.execution_identity.workspace_owner_id
            or context.context_scope!=state.execution_identity.context_scope
            or context.personal_context_allowed!=state.execution_identity.personal_context_allowed
            or context.org_id!=(str(state.org_id) if state.org_id else None)
            or context.conversation_id!=str(state.conversation_id)):raise PermissionError()
        raw=decode_raw(extension)
        ref=raw.file_ref if extension['kind']=='agent' else None
        if ref is None:raise TaskError('TASK_NATIVE_FILE_UNAVAILABLE')
        files=FileExecutor(get_settings().file_workspace_root,context.workspace_owner_id,context.org_id,create_root=False)
        # Native FileRef exports legitimately live in system staging. The user
        # file-tools resolver forbids opening that directory, so retain the
        # existing result-consumer containment contract here instead.
        if not Path(ref.path).is_absolute():raise ValueError()
        path=Path(ref.path).resolve(strict=True)
        relative=path.relative_to(Path(files.workspace_root)).as_posix()
        # FileRef's public locator must name precisely the native file. A
        # filename alone cannot alias a different resource in the workspace.
        if (Path(ref.filename).name!=ref.filename or relative!=ref.sandbox_ref
            or not ref.is_valid() or path.stat().st_size!=ref.size_bytes):raise ValueError()
        boundary=ResourceAccessBoundary.from_dict(thaw(context.resource_access))
        boundary.require('read',relative)
        checksum=content_digest(path)
        # Recheck the current scope after reading as authorization may expire.
        boundary.require('read',relative)
        if Path(ref.path).resolve(strict=True)!=path or not ref.is_valid():raise ValueError()
        metadata={k:v for k,v in extension['raw']['file_ref'].items() if k!='path'}
        value={'workspace_path':relative,'content_sha256':checksum,'file_ref':metadata}
        return digest(value),value
    except TaskError:raise
    except (ValueError,TypeError,KeyError,AttributeError,OSError,PermissionError):
        raise TaskError('TASK_NATIVE_FILE_UNVERIFIED') from None
