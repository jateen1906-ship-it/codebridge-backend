"""File extension filtering, excluded directory detection, & traversal safety tests."""

from __future__ import annotations

import pytest

from app.core.exceptions import BadRequestError
from app.services.file_filter_service import FileFilterService, is_excluded_directory


def test_supported_source_files_are_accepted(base_test_settings) -> None:
    svc = FileFilterService(base_test_settings)
    for path, expected_lang in (
        ("app/main.py", "python"),
        ("src/index.tsx", "typescript"),
        ("pkg/handler.go", "go"),
        ("src/Service.java", "java"),
        ("lib/core.rs", "rust"),
        ("README.md", "markdown"),
        ("schema.sql", "sql"),
        ("deploy.sh", "shell"),
    ):
        decision = svc.evaluate_file(path, declared_size=512)
        assert decision.allowed is True
        assert decision.language == expected_lang


def test_excluded_directories_and_artifacts_are_rejected(base_test_settings) -> None:
    svc = FileFilterService(base_test_settings)
    for excluded_dir in (".git", "node_modules", "__pycache__", ".venv", "dist", "build", "target"):
        assert is_excluded_directory(excluded_dir) is True
        decision = svc.evaluate_file(f"{excluded_dir}/file.py", declared_size=100)
        assert decision.allowed is False
        assert decision.skip_reason == "excluded_directory"

    for artifact in ("package-lock.json", "yarn.lock", "static/app.min.js", "static/app.js.map"):
        decision = svc.evaluate_file(artifact, declared_size=200)
        assert decision.allowed is False
        assert decision.skip_reason == "lockfile_or_artifact"

    binary_decision = svc.evaluate_file("assets/diagram.png", declared_size=100)
    assert binary_decision.allowed is False
    assert binary_decision.skip_reason == "binary_file"


def test_oversized_and_unsupported_extensions_rejected(base_test_settings) -> None:
    svc = FileFilterService(base_test_settings)
    oversized = svc.evaluate_file(
        "app/huge.py",
        declared_size=base_test_settings.MAX_FILE_BYTES + 1,
    )
    assert oversized.allowed is False
    assert oversized.skip_reason == "oversized_file"

    unsupported = svc.evaluate_file("notes/archive.xyz", declared_size=100)
    assert unsupported.allowed is False
    assert unsupported.skip_reason == "unsupported_extension"


def test_unsafe_path_traversal_rejected(base_test_settings) -> None:
    svc = FileFilterService(base_test_settings)
    for bad_path in ("../secret.py", "app/../../etc/passwd", "app/%2e%2e/foo.py", "app\\win.py"):
        with pytest.raises(BadRequestError):
            svc.evaluate_file(bad_path)

