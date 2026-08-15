"""Envelope encryption primitives for configuration Secret payloads."""

from __future__ import annotations

from abc import ABC, abstractmethod
import base64
import binascii
from dataclasses import dataclass
import json
import os
import re
from typing import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


_NONCE_BYTES = 12
_KEY_BYTES = 32
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_CURRENT_VERSION_ENV = "CONFIG_KEK_CURRENT_VERSION"
_KEYRING_ENV = "CONFIG_KEK_KEYRING_JSON"


class SecretMaterialError(RuntimeError):
    """Stable failure for malformed or unauthentic Secret material."""


class KekVersionMissingError(SecretMaterialError):
    """Raised when an envelope references a KEK outside the active keyring."""


@dataclass(frozen=True)
class SecretEnvelope:
    """Database-safe encrypted payload and wrapped per-record DEK."""

    payload_ciphertext: str
    wrapped_dek: str
    kek_version: str
    payload_version: int


class KeyEncryptionProvider(ABC):
    """Replaceable boundary for wrapping per-record data encryption keys."""

    @property
    @abstractmethod
    def current_version(self) -> str:
        """Return the KEK version used for new writes."""

    @abstractmethod
    def wrap_dek(self, dek: bytes) -> tuple[str, str]:
        """Wrap a 32-byte DEK and return ciphertext plus KEK version."""

    @abstractmethod
    def unwrap_dek(self, wrapped_dek: str, kek_version: str) -> bytes:
        """Unwrap a DEK using the referenced KEK version."""


class LocalKEKProvider(KeyEncryptionProvider):
    """AES-GCM KEK keyring loaded from a dedicated process environment."""

    def __init__(
        self,
        *,
        current_version: str,
        keyring: Mapping[str, bytes],
    ) -> None:
        if not _VERSION_PATTERN.fullmatch(current_version):
            raise ValueError("CONFIG_KEK_CURRENT_VERSION_INVALID")
        normalized = dict(keyring)
        if current_version not in normalized:
            raise ValueError("CONFIG_KEK_CURRENT_VERSION_MISSING")
        if any(
            not _VERSION_PATTERN.fullmatch(version) or len(key) != _KEY_BYTES
            for version, key in normalized.items()
        ):
            raise ValueError("CONFIG_KEK_KEYRING_INVALID")
        self._current_version = current_version
        self._keyring = normalized

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "LocalKEKProvider":
        """Build a validated keyring without loading the shared Settings."""
        values = os.environ if environ is None else environ
        current_version = values.get(_CURRENT_VERSION_ENV, "")
        raw_keyring = values.get(_KEYRING_ENV, "")
        try:
            parsed = json.loads(raw_keyring)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("CONFIG_KEK_KEYRING_INVALID") from error
        if not isinstance(parsed, dict) or not parsed:
            raise ValueError("CONFIG_KEK_KEYRING_INVALID")

        keyring: dict[str, bytes] = {}
        try:
            for version, encoded_key in parsed.items():
                if not isinstance(version, str) or not isinstance(
                    encoded_key, str
                ):
                    raise ValueError("CONFIG_KEK_KEYRING_INVALID")
                keyring[version] = base64.b64decode(
                    encoded_key,
                    validate=True,
                )
        except (binascii.Error, ValueError) as error:
            raise ValueError("CONFIG_KEK_KEYRING_INVALID") from error
        return cls(current_version=current_version, keyring=keyring)

    @property
    def current_version(self) -> str:
        return self._current_version

    def wrap_dek(self, dek: bytes) -> tuple[str, str]:
        if len(dek) != _KEY_BYTES:
            raise ValueError("SECRET_DEK_INVALID")
        version = self.current_version
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = AESGCM(self._keyring[version]).encrypt(
            nonce,
            dek,
            self._wrap_aad(version),
        )
        return (
            base64.b64encode(nonce + ciphertext).decode("ascii"),
            version,
        )

    def unwrap_dek(self, wrapped_dek: str, kek_version: str) -> bytes:
        key = self._keyring.get(kek_version)
        if key is None:
            raise KekVersionMissingError("KEK_VERSION_MISSING")
        try:
            raw = base64.b64decode(wrapped_dek, validate=True)
            if len(raw) <= _NONCE_BYTES:
                raise ValueError("wrapped DEK too short")
            dek = AESGCM(key).decrypt(
                raw[:_NONCE_BYTES],
                raw[_NONCE_BYTES:],
                self._wrap_aad(kek_version),
            )
        except (binascii.Error, InvalidTag, ValueError) as error:
            raise SecretMaterialError(
                "SECRET_MATERIAL_UNAVAILABLE"
            ) from error
        if len(dek) != _KEY_BYTES:
            raise SecretMaterialError("SECRET_MATERIAL_UNAVAILABLE")
        return dek

    @staticmethod
    def _wrap_aad(version: str) -> bytes:
        return f"everydayai:kek-wrap:{version}".encode("ascii")
