"""Language-aware LangChain chunking and line-range tracking tests."""

from __future__ import annotations

from app.core.config import Settings
from app.services.chunking_service import ChunkingService


def test_python_chunking_produces_accurate_monotonic_line_spans(base_test_settings: Settings) -> None:
    cfg = base_test_settings.model_copy(update={"CHUNK_SIZE": 220, "CHUNK_OVERLAP": 40})
    chunker = ChunkingService(cfg)

    source = "\n".join(
        [
            "def first_helper(x: int) -> int:",
            "    total = x + 1",
            "    return total",
            "",
            "def second_handler(items: list[str]) -> list[str]:",
            "    cleaned = [item.strip().lower() for item in items if item]",
            "    return sorted(cleaned)",
            "",
            "class PaymentCoordinator:",
            "    def execute(self, invoice_id: str) -> dict:",
            "        status = 'PAID' if invoice_id else 'PENDING'",
            "        return {'invoice_id': invoice_id, 'status': status}",
        ]
    )

    chunks = chunker.chunk_file(
        file_path="app/payments/coordinator.py",
        language="python",
        text=source,
    )
    assert len(chunks) >= 2
    lines = source.split("\n")

    for idx, chunk in enumerate(chunks):
        assert chunk.chunk_index == idx
        assert chunk.start_line >= 1
        assert chunk.end_line >= chunk.start_line
        assert chunk.end_line <= len(lines)
        assert len(chunk.content_hash) == 64
        assert chunk.chunk_identifier.startswith("app/payments/coordinator.py#")
        if idx > 0:
            assert chunk.start_line >= chunks[idx - 1].start_line


def test_chunking_supports_js_and_ignores_empty_text(base_test_settings: Settings) -> None:
    chunker = ChunkingService(base_test_settings)
    assert chunker.chunk_file(file_path="empty.ts", language="typescript", text="   \n  ") == []

    js_chunks = chunker.chunk_file(
        file_path="src/client.js",
        language="javascript",
        text="export function connect() {\n  return true;\n}\n",
    )
    assert len(js_chunks) == 1
    assert js_chunks[0].start_line == 1
    assert js_chunks[0].end_line == 3

