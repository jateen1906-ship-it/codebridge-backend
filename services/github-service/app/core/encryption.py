"""Authenticated AES-256-GCM encryption at rest for GitHub OAuth access tokens."""

from __future__ import annotations

import base64
import os
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_TOKEN_VERSION_PREFIX = "v1:"
_NONCE_SIZE_BYTES = 12
_MIN_CT_SIZE_BYTES = _NONCE_SIZE_BYTES + 16
REVOKED_TOKEN_SENTINEL = "[REVOKED]"


class TokenEncryptionError(Exception):
    """Raised when encrypting or decrypting an OAuth token fails safely."""


class TokenEncryptionService:
    """AES-256-GCM cipher binding ciphertext integrity to owner_id associated data."""

    def __init__(self, key_bytes: bytes) -> None:
        if len(key_bytes) != 32:
            raise ValueError("AES-256-GCM key must be exactly 32 bytes.")
        self._aesgcm = AESGCM(key_bytes)

    @staticmethod
    def _build_aad(owner_id: uuid.UUID) -> bytes:
        return f"codebridge:github-oauth-token:{uuid.UUID(str(owner_id))}".encode("utf-8")

    def encrypt(self, plaintext_token: str, *, owner_id: uuid.UUID) -> str:
        """Encrypt a plaintext OAuth access token using a fresh 96-bit nonce."""
        if not plaintext_token or not plaintext_token.strip():
            raise TokenEncryptionError("Cannot encrypt an empty OAuth access token.")
        nonce = os.urandom(_NONCE_SIZE_BYTES)
        aad = self._build_aad(owner_id)
        try:
            ciphertext = self._aesgcm.encrypt(nonce, plaintext_token.encode("utf-8"), aad)
        except Exception as exc:
            raise TokenEncryptionError("Failed to encrypt OAuth token.") from exc

        encoded = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")
        return f"{_TOKEN_VERSION_PREFIX}{encoded}"

    def decrypt(self, encrypted_token: str, *, owner_id: uuid.UUID) -> str:
        """Decrypt an encrypted OAuth token and verify owner binding and authentication tag."""
        if not encrypted_token or encrypted_token == REVOKED_TOKEN_SENTINEL:
            raise TokenEncryptionError("Stored GitHub OAuth credential is unavailable or revoked.")
        if not encrypted_token.startswith(_TOKEN_VERSION_PREFIX):
            raise TokenEncryptionError("Unsupported encrypted token format.")

        encoded_payload = encrypted_token[len(_TOKEN_VERSION_PREFIX) :]
        try:
            raw = base64.urlsafe_b64decode(encoded_payload.encode("ascii"))
        except Exception as exc:
            raise TokenEncryptionError("Malformed encrypted OAuth token payload.") from exc

        if len(raw) < _MIN_CT_SIZE_BYTES:
            raise TokenEncryptionError("Corrupted encrypted OAuth token length.")

        nonce = raw[:_NONCE_SIZE_BYTES]
        ciphertext = raw[_NONCE_SIZE_BYTES:]
        aad = self._build_aad(owner_id)
        try:
            plaintext_bytes = self._aesgcm.decrypt(nonce, ciphertext, aad)
            plaintext = plaintext_bytes.decode("utf-8")
        except (InvalidTag, UnicodeDecodeError) as exc:
            raise TokenEncryptionError("Failed to decrypt or authenticate OAuth token.") from exc

        if not plaintext:
            raise TokenEncryptionError("Decrypted OAuth token is empty.")
        return plaintext

