"""Safe decoding, NUL/binary detection, size enforcement, & secret screening for repo files."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass

from app.clients.github_client import GitHubContentNode
from app.core.config import Settings
from app.services.file_filter_service import detect_secret_in_content


@dataclass(frozen=True, slots=True)
class ParsedFileResult:
    """Validated normalized source text ready for chunking, or an explicit skip outcome."""

    accepted: bool
    text: str | None = None
    size_bytes: int = 0
    skip_reason: str | None = None


class FileParserService:
    """Parse decoded or base64 repository file payloads into clean UTF-8 source text."""

    def __init__(self, settings: Settings) -> None:
        self._max_file_bytes = settings.MAX_FILE_BYTES

    def parse(self, node: GitHubContentNode) -> ParsedFileResult:
        """Validate encoding, binary safety, size cap, non-emptiness, & secret content."""
        if node.is_binary:
            return ParsedFileResult(accepted=False, skip_reason="binary_file")

        if node.truncated or (node.size is not None and node.size > self._max_file_bytes):
            return ParsedFileResult(
                accepted=False,
                size_bytes=int(node.size or 0),
                skip_reason="oversized_file",
            )

        if node.content is None:
            return ParsedFileResult(
                accepted=False,
                size_bytes=int(node.size or 0),
                skip_reason="empty_file",
            )

        raw_text: str
        encoding = (node.encoding or "utf-8").lower()
        if encoding == "base64":
            try:
                raw_bytes = base64.b64decode(node.content, validate=True)
            except (binascii.Error, ValueError):
                return ParsedFileResult(accepted=False, skip_reason="decode_error")
            if b"\x00" in raw_bytes:
                return ParsedFileResult(accepted=False, size_bytes=len(raw_bytes), skip_reason="binary_file")
            if len(raw_bytes) > self._max_file_bytes:
                return ParsedFileResult(
                    accepted=False,
                    size_bytes=len(raw_bytes),
                    skip_reason="oversized_file",
                )
            try:
                raw_text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                return ParsedFileResult(accepted=False, size_bytes=len(raw_bytes), skip_reason="binary_file")
        else:
            raw_text = node.content
            if "\x00" in raw_text:
                return ParsedFileResult(accepted=False, skip_reason="binary_file")

        byte_size = len(raw_text.encode("utf-8"))
        if byte_size > self._max_file_bytes:
            return ParsedFileResult(
                accepted=False,
                size_bytes=byte_size,
                skip_reason="oversized_file",
            )

        normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
        if not normalized.strip():
            return ParsedFileResult(
                accepted=False,
                size_bytes=byte_size,
                skip_reason="empty_file",
            )

        secret_kind = detect_secret_in_content(normalized)
        if secret_kind is not None:
            return ParsedFileResult(
                accepted=False,
                size_bytes=byte_size,
                skip_reason="secret_detected",
            )

        return ParsedFileResult(
            accepted=True,
            text=normalized,
            size_bytes=byte_size,
            skip_reason=None,
        )

