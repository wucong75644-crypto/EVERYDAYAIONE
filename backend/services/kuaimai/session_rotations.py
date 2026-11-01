"""Record observed SDK refresh relationships without storing credentials."""
import asyncio
from uuid import UUID

from core.db_scope import (
    AsyncScopedDatabaseClient,
    DatabaseAccessKind,
    DatabaseScope,
    ScopedDatabaseClient,
    database_scope_from_client,
)
from core.local_db import AsyncLocalDBClient, LocalDBClient
from core.org_scoped_db import OrgScopedDB


_ALLOWED_ACCESS = {
    DatabaseAccessKind.RUNTIME,
    DatabaseAccessKind.RUNTIME_ADMIN,
    DatabaseAccessKind.WORKER,
}


def rotation_recorder(db, org_id, actor_user_id=None):
    # Runtime calls retain their current identity. Tenant-neutral workers may
    # narrow their existing authority to the organization they loaded.
    try:
        org_id = str(UUID(str(org_id)))
        actor = str(UUID(str(actor_user_id))) if actor_user_id else None
    except (ValueError, TypeError):
        return None

    if isinstance(db, OrgScopedDB):
        if db.org_id is not None and str(db.org_id) != org_id:
            return None
        db = db._db

    current = database_scope_from_client(db)
    if current is not None:
        if current.access_kind not in _ALLOWED_ACCESS:
            return None
        if current.org_id != org_id and not (
            current.access_kind == DatabaseAccessKind.WORKER
            and current.org_id is None
            and current.actor_user_id is None
        ):
            return None
        if actor is not None and current.actor_user_id != actor:
            return None
        scope = (
            DatabaseScope(None, org_id, DatabaseAccessKind.WORKER)
            if current.org_id is None else current
        )
    else:
        scope = DatabaseScope(
            actor, org_id,
            DatabaseAccessKind.RUNTIME if actor else DatabaseAccessKind.WORKER,
        )

    if isinstance(db, (AsyncLocalDBClient, AsyncScopedDatabaseClient)):
        client = AsyncScopedDatabaseClient(db, scope)
        asynchronous = True
    elif isinstance(db, (LocalDBClient, ScopedDatabaseClient)):
        client = ScopedDatabaseClient(db, scope)
        asynchronous = False
    else:
        # Unknown/legacy transports cannot assert a database identity.
        return None

    async def record(observed_org, old, new):
        if str(observed_org) != org_id:
            raise PermissionError('ERP_REFRESH_SCOPE_MISMATCH')
        parameters = {
            'p_org_id': org_id,
            'p_old_fingerprint': old,
            'p_new_fingerprint': new,
        }
        if asynchronous:
            response = await client.rpc(
                'record_agent_erp_session_rotation', parameters,
            ).execute()
        else:
            response = await asyncio.to_thread(
                lambda: client.rpc(
                    'record_agent_erp_session_rotation', parameters,
                ).execute()
            )
        if not response or not isinstance(response.data, dict) or response.data.get('outcome') != 'recorded':
            raise RuntimeError('ERP_REFRESH_RECORD_UNCONFIRMED')

    return record
