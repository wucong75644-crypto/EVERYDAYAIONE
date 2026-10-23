"""Immutable knowledge inputs copied into the current actor's task workspace."""
import os
from pathlib import Path
from core.config import get_settings
from services.file_executor import FileExecutor
from services.workspace_coordination import workspace_lock_sync
from .files import read_version, read_regular, clean_name


def relative_path(run,item,version):
    return f"knowledge-inputs/{run.id}/{item['handle']}/{clean_name(version['name'])}"


def materialize(run,item,version):
    settings=get_settings()
    executor=FileExecutor(settings.file_workspace_root,run.repo.scope.actor_user_id,run.repo.scope.org_id)
    relative=relative_path(run,item,version)
    with workspace_lock_sync(executor.workspace_root,write=True):
        # Refuse every symlink component, including a pre-existing actor root.
        root=Path(executor.workspace_root)
        probe=root
        for parent in [root,*root.parents]:
            if parent.is_symlink():raise PermissionError('KNOWLEDGE_STAGING_UNAVAILABLE')
        for part in Path(relative).parts[:-1]:
            probe=probe/part
            if probe.is_symlink():raise PermissionError('KNOWLEDGE_STAGING_UNAVAILABLE')
            probe.mkdir(mode=0o700,exist_ok=True)
        target=probe/Path(relative).name
        data=read_version(settings.file_workspace_root,version)
        try:
            fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        except FileExistsError:
            if read_regular(target)!=data:raise PermissionError('KNOWLEDGE_STAGING_CHANGED')
        else:
            with os.fdopen(fd,'wb') as f:f.write(data)
    return relative


def cleanup(run):
    """Remove only this snapshot's known input copies, never arbitrary workspace data.

    Receipts stay durable; resumed execution reauthorizes and recreates its copies.
    """
    settings=get_settings()
    executor=FileExecutor(settings.file_workspace_root,run.repo.scope.actor_user_id,run.repo.scope.org_id)
    root=Path(executor.workspace_root)
    with workspace_lock_sync(executor.workspace_root,write=True):
        if any(p.is_symlink() for p in [root,*root.parents]):return
        for item in run.snapshot['items']:
            if item['type']!='file':continue
            relative=relative_path(run,item,{'name':item['title']})
            target=root/relative
            if any(p.is_symlink() for p in [target,*target.parents] if p!=root.parent):continue
            target.unlink(missing_ok=True)
            for parent in (target.parent,target.parent.parent,target.parent.parent.parent):
                try:parent.rmdir()
                except OSError:break
