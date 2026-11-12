"""File decoding, base64 handling, NUL/binary checks, & size boundary tests."""

from __future__ import annotations

import base64
from typing import Any

from app.clients.github_client import GitHubContentNode
from app.services.file_parser_service import FileParserService
from tests.conftest import PROJECT_ID


def _make_node(**overrides: Any) -> GitHubContentNode:
    base: dict[str, Any] = {
        "project_id": PROJECT_ID,
        "full_name": "owner/repo",
        "ref": "main",
        "path": "app/service.py",
        "kind": "file",
        "size": 32,
        "encoding": "utf-8",
        "is_binary": False,
        "truncated": False,
        "content": "def ping():\r\n    return 'pong'\r\n",
    }
    base.update(overrides)
    return GitHubContentNode(**base)


def test_parse_normalizes_crlf_and_decodes_base64(base_test_settings) -> None:
    parser = FileParserService(base_test_settings)
    res_utf8 = parser.parse(_make_node())
    assert res_utf8.accepted is True
    assert res_utf8.text == "def ping():\n    return 'pong'\n"

    encoded = base64.b64encode(b"print('hello')\n").decode("ascii")
    res_b64 = parser.parse(_make_node(encoding="base64", content=encoded, size=15))
    assert res_b64.accepted is True
    assert res_b64.text == "print('hello')\n"


def test_parse_rejects_binary_truncated_empty_and_oversized(base_test_settings) -> None:
    parser = FileParserService(base_test_settings)

    assert parser.parse(_make_node(is_binary=True)).skip_reason == "binary_file"
    assert parser.parse(_make_node(truncated=True)).skip_reason == "oversized_file"
    assert parser.parse(_make_node(content="   \n\t\r\n  ")).skip_reason == "empty_file"

    nul_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n\x00\x00").decode("ascii")
    assert parser.parse(_make_node(encoding="base64", content=nul_b64, size=10)).skip_reason == "binary_file"

    huge = "x = 1\n" * (base_test_settings.MAX_FILE_BYTES // 2)
    assert parser.parse(_make_node(content=huge, size=len(huge))).skip_reason == "oversized_file"

