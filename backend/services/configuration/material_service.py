"""Per-record Secret payload encryption and authenticated decryption."""

from __future__ import annotations

import base64
import binascii
import json
import os
from typing import Mapping
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from services.configuration.definitions import ScopeKind
from services.configuration.envelope import (
    KeyEncryptionProvider,
    SecretEnvelope,
    SecretMaterialError,
)


_NONCE_BYTES = 12
_DEK_BYTES = 32


class SecretMaterialService:
    """Encrypt and decrypt JSON Secret payloads with scope-bound AAD."""

    def __init__(self, provider: KeyEncryptionProvider) -> None:
        self._provider = provider

    def __repr__(self) -> str:
        return "SecretMaterialService(<redacted>)"

    def __getstate__(self) -> Mapping[str, object]:
        raise TypeError("SECRET_MATERIAL_SERVICE_NOT_SERIALIZABLE")

    def __reduce__(self) -> object:
        raise TypeError("SECRET_MATERIAL_SERVICE_NOT_SERIALIZABLE")

    def __reduce_ex__(self, _protocol: int) -> object:
        raise TypeError("SECRET_MATERIAL_SERVICE_NOT_SERIALIZABLE")

    def __copy__(self) -> "SecretMaterialService":
        raise TypeError("SECRET_MATERIAL_SERVICE_NOT_COPYABLE")

    def __deepcopy__(self, _memo: object) -> "SecretMaterialService":
        raise TypeError("SECRET_MATERIAL_SERVICE_NOT_COPYABLE")

    def encrypt_payload(
        self,
        *,
        scope_kind: ScopeKind,
        scope_id: str | None,
        secret_name: str,
        payload_version: int,
        payload: Mapping[str, object],
    ) -> SecretEnvelope:
        normalized_scope_id = self._validate_context(
            scope_kind,
            scope_id,
            secret_name,
            payload_version,
        )
        if not payload:
            raise ValueError("CONFIG_VALUE_INVALID")
        try:
            plaintext = json.dumps(
                dict(payload),
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ValueError("CONFIG_VALUE_INVALID") from error

        dek = os.urandom(_DEK_BYTES)
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = AESGCM(dek).encrypt(
            nonce,
            plaintext,
            self._payload_aad(
                scope_kind,
                normalized_scope_id,
                secret_name,
                payload_version,
            ),
        )
        wrapped_dek, kek_version = self._provider.wrap_dek(dek)
        return SecretEnvelope(
            payload_ciphertext=base64.b64encode(
                nonce + ciphertext
            ).decode("ascii"),
            wrapped_dek=wrapped_dek,
            kek_version=kek_version,
            payload_version=payload_version,
        )

    def decrypt_payload(
        self,
        envelope: SecretEnvelope,
        *,
        scope_kind: ScopeKind,
        scope_id: str | None,
        secret_name: str,
    ) -> dict[str, object]:
        normalized_scope_id = self._validate_context(
            scope_kind,
            scope_id,
            secret_name,
            envelope.payload_version,
        )
        try:
            raw = base64.b64decode(
                envelope.payload_ciphertext,
                validate=True,
            )
            if len(raw) <= _NONCE_BYTES:
                raise ValueError("ciphertext too short")
            dek = self._provider.unwrap_dek(
                envelope.wrapped_dek,
                envelope.kek_version,
            )
            plaintext = AESGCM(dek).decrypt(
                raw[:_NONCE_BYTES],
                raw[_NONCE_BYTES:],
                self._payload_aad(
                    scope_kind,
                    normalized_scope_id,
                    secret_name,
                    envelope.payload_version,
                ),
            )
            payload = json.loads(plaintext)
        except SecretMaterialError:
            raise
        except (
            binascii.Error,
            InvalidTag,
            UnicodeDecodeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            raise SecretMaterialError(
                "SECRET_MATERIAL_UNAVAILABLE"
            ) from error
        if not isinstance(payload, dict):
            raise SecretMaterialError("SECRET_MATERIAL_UNAVAILABLE")
        return payload

    @staticmethod
    def _validate_context(
        scope_kind: ScopeKind,
        scope_id: str | None,
        secret_name: str,
        payload_version: int,
    ) -> str:
        if (
            not secret_name
            or secret_name != secret_name.strip()
            or len(secret_name) > 120
            or payload_version < 1
        ):
            raise ValueError("CONFIG_VALUE_INVALID")
        if scope_kind == "platform":
            if scope_id is not None:
                raise ValueError("CONFIG_SCOPE_FORBIDDEN")
            return ""
        if scope_kind not in ("organization", "user") or scope_id is None:
            raise ValueError("CONFIG_SCOPE_FORBIDDEN")
        try:
            return str(UUID(scope_id))
        except ValueError as error:
            raise ValueError("CONFIG_SCOPE_FORBIDDEN") from error

    @staticmethod
    def _payload_aad(
        scope_kind: ScopeKind,
        scope_id: str,
        secret_name: str,
        payload_version: int,
    ) -> bytes:
        return json.dumps(
            [scope_kind, scope_id, secret_name, payload_version],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
