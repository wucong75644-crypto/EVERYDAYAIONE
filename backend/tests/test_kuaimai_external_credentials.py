"""快麦外部凭证解析与保存入口回归测试。"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.routes.kuaimai_external_credentials import (
    CreateCredentialIn,
    create_credential,
)
from core.exceptions import AppException
from services.configuration.external_control import ExternalCredential
from services.kuaimai_external.curl_parser import (
    detect_source,
    is_kuaimai_host,
    parse_curl,
)


def _curl(
    url: str = "https://erp.superboss.cc/kmzk/profit/report/shop",
) -> str:
    return (
        f"curl '{url}' "
        "--header='CompanyId: 65109' "
        "--cookie='session=x; _censeid=cense-token; other=y'"
    )


def test_parse_curl_supports_equals_flags_and_safari_quotes():
    parsed = parse_curl(
        "curl $'https://erp.superboss.cc/report/sale/list' "
        "--header=$'CompanyId: 65109' "
        "--header=$'Cookie: session=x; _censeid=cense-token'"
    )

    assert parsed.companyid == 65109
    assert parsed.censeid == "cense-token"
    assert detect_source(parsed) == "viperp"
    assert is_kuaimai_host(parsed) is True


def test_kuaimai_host_check_rejects_lookalike_domain():
    parsed = parse_curl(
        "curl 'https://erp.superboss.cc.evil.example/kmzk/report' "
        "-H 'companyid: 65109' -b '_censeid=token'"
    )

    assert is_kuaimai_host(parsed) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("curl_text", "code"),
    [
        (
            "curl 'https://example.com/kmzk' "
            "-H 'companyid: 65109' -b '_censeid=token'",
            "KUAIMAI_HOST_INVALID",
        ),
        (
            "curl 'https://erp.superboss.cc/kmzk/report' "
            "-b '_censeid=token'",
            "KUAIMAI_COMPANY_ID_MISSING",
        ),
        (
            "curl 'https://erp.superboss.cc/kmzk/report' "
            "-H 'companyid: 65109'",
            "KUAIMAI_CENSEID_MISSING",
        ),
        (
            _curl("https://erp.superboss.cc/report/sale/list"),
            "KUAIMAI_SOURCE_MISMATCH",
        ),
    ],
)
async def test_create_credential_rejects_invalid_material(
    curl_text: str,
    code: str,
):
    context = SimpleNamespace(
        org_id="00000000-0000-0000-0000-000000000001",
        org_role="admin",
        user_id="user-1",
    )

    with pytest.raises(AppException) as raised:
        await create_credential(
            CreateCredentialIn(curl_text=curl_text, source="thinktank"),
            context,
            SimpleNamespace(),
        )

    assert raised.value.code == code


@pytest.mark.asyncio
async def test_create_credential_saves_and_returns_read_back_bundle():
    now = datetime.now(timezone.utc)
    credential = ExternalCredential(
        id="thinktank",
        org_id="00000000-0000-0000-0000-000000000001",
        source="thinktank",
        kuaimai_company_id=65109,
        censeid_cookie="cense-token",
        cookie_full="session=x; _censeid=cense-token; other=y",
        status="active",
        last_health_check_at=None,
        last_sync_at=None,
        last_sync_status=None,
        last_sync_error=None,
        created_at=now,
        updated_at=now,
    )
    context = SimpleNamespace(
        org_id=credential.org_id,
        org_role="owner",
        user_id="user-1",
    )
    control = AsyncMock()
    control.set.return_value = credential

    with patch(
        "api.routes.kuaimai_external_credentials.ExternalConfigurationControl",
        return_value=control,
    ):
        result = await create_credential(
            CreateCredentialIn(curl_text=_curl(), source="thinktank"),
            context,
            SimpleNamespace(),
        )

    control.set.assert_awaited_once_with(
        org_id=credential.org_id,
        source="thinktank",
        company_id=65109,
        censeid_cookie="cense-token",
        cookie_full="session=x; _censeid=cense-token; other=y",
    )
    assert result.credential.id == "thinktank"
    assert result.detected_companyid == 65109


@pytest.mark.asyncio
async def test_create_credential_maps_control_plane_failure():
    context = SimpleNamespace(
        org_id="00000000-0000-0000-0000-000000000001",
        org_role="admin",
        user_id="user-1",
    )
    control = AsyncMock()
    control.set.side_effect = RuntimeError("database detail must not leak")

    with patch(
        "api.routes.kuaimai_external_credentials.ExternalConfigurationControl",
        return_value=control,
    ), pytest.raises(AppException) as raised:
        await create_credential(
            CreateCredentialIn(curl_text=_curl(), source="thinktank"),
            context,
            SimpleNamespace(),
        )

    assert raised.value.code == "KUAIMAI_CONFIG_SAVE_FAILED"
    assert "database detail" not in raised.value.message
