"""Language-aware LangChain code splitting with exact 1-based line tracking."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from langchain_text_splitters import Language, RecursiveCharacterTextSplitter

from app.core.config import Settings

_LANGCHAIN_LANGUAGE_MAP: dict[str, Language] = {
    "python": Language.PYTHON,
    "javascript": Language.JS,
    "typescript": Language.TS,
    "go": Language.GO,
    "java": Language.JAVA,
    "c": Language.C,
    "cpp": Language.CPP,
    "csharp": Language.CSHARP,
    "php": Language.PHP,
    "ruby": Language.RUBY,
    "rust": Language.RUST,
    "html": Language.HTML,
    "markdown": Language.MARKDOWN,
}


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """In-memory split code chunk ready for embedding, ChromaDB upsert, and metadata persistence."""

    chunk_index: int
    file_path: str
    language: str
    start_line: int
    end_line: int
    content: str
    content_hash: str
    chunk_identifier: str
    token_estimate: int


class ChunkingService:
    """Split normalized source files into bounded, line-anchored, deduplicated chunks."""

    def __init__(self, settings: Settings) -> None:
        self._chunk_size = settings.CHUNK_SIZE
        self._chunk_overlap = settings.CHUNK_OVERLAP

    def _build_splitter(self, language: str) -> RecursiveCharacterTextSplitter:
        lc_language = _LANGCHAIN_LANGUAGE_MAP.get(language.lower())
        if lc_language is not None:
            return RecursiveCharacterTextSplitter.from_language(
                language=lc_language,
                chunk_size=self._chunk_size,
                chunk_overlap=self._chunk_overlap,
            )
        return RecursiveCharacterTextSplitter(
            chunk_size=self._chunk_size,
            chunk_overlap=self._chunk_overlap,
            separators=["\n\n", "\n", " ", ""],
        )

    def chunk_file(
        self,
        *,
        file_path: str,
        language: str,
        text: str,
    ) -> list[ChunkDraft]:
        """Split a normalized file into ordered chunks with accurate line spans."""
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        if not normalized.strip():
            return []

        splitter = self._build_splitter(language)
        raw_pieces = splitter.split_text(normalized)
        if not raw_pieces:
            return []

        chunks: list[ChunkDraft] = []
        seen_hashes: set[str] = set()
        cursor = 0
        total_lines = normalized.count("\n") + 1

        for raw_piece in raw_pieces:
            stripped_piece = raw_piece.strip("\n")
            if not stripped_piece.strip():
                continue

            # Locate chunk span deterministically starting near prior cursor (allowing overlap).
            search_from = max(0, cursor - self._chunk_overlap - 32)
            idx = normalized.find(stripped_piece, search_from)
            if idx == -1:
                idx = normalized.find(raw_piece, search_from)
            if idx == -1:
                idx = normalized.find(stripped_piece)

            if idx != -1:
                start_line = normalized.count("\n", 0, idx) + 1
                end_offset = idx + max(len(stripped_piece) - 1, 0)
                end_line = normalized.count("\n", 0, end_offset) + 1
                cursor = idx + len(stripped_piece)
            else:
                start_line = min(max(1, normalized.count("\n", 0, cursor) + 1), total_lines)
                span_lines = max(1, stripped_piece.count("\n") + 1)
                end_line = min(total_lines, start_line + span_lines - 1)

            content_hash = hashlib.sha256(stripped_piece.encode("utf-8")).hexdigest()
            dedupe_key = f"{start_line}:{end_line}:{content_hash}"
            if dedupe_key in seen_hashes:
                continue
            seen_hashes.add(dedupe_key)

            chunk_index = len(chunks)
            chunk_identifier = f"{file_path}#{chunk_index}:{start_line}-{end_line}:{content_hash[:12]}"
            token_estimate = max(1, math.ceil(len(stripped_piece) / 4))

            chunks.append(
                ChunkDraft(
                    chunk_index=chunk_index,
                    file_path=file_path,
                    language=language,
                    start_line=start_line,
                    end_line=max(start_line, end_line),
                    content=stripped_piece,
                    content_hash=content_hash,
                    chunk_identifier=chunk_identifier,
                    token_estimate=token_estimate,
                )
            )

        return chunks

