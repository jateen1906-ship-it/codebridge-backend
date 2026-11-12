"""Tests for archive security validation: zip-slip, symlinks, zip bombs, and .git stripping."""

from __future__ import annotations

import io
import stat
import zipfile
from pathlib import Path

import pytest

from app.core.exceptions import UnsafeArchiveError
from app.services.archive_service import ArchiveExtractor
from tests.conftest import build_test_settings


def _make_zip(entries: list[tuple[str, bytes, int]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data, mode in entries:
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            zf.writestr(info, data)
    return buf.getvalue()


def test_blocks_path_traversal_symlinks_and_absolute_paths(tmp_path: Path) -> None:
    extractor = ArchiveExtractor(build_test_settings(tmp_path))

    traversal_zip = _make_zip([("repo-main/../../etc/passwd", b"root:x:0:0", 0o100644)])
    with pytest.raises(UnsafeArchiveError):
        extractor.extract_zip_bytes(traversal_zip, destination_dir=tmp_path / "ws1")

    abs_zip = _make_zip([("/etc/shadow", b"bad", 0o100644)])
    with pytest.raises(UnsafeArchiveError):
        extractor.extract_zip_bytes(abs_zip, destination_dir=tmp_path / "ws2")

    symlink_zip = _make_zip([("repo-main/link", b"/etc/passwd", stat.S_IFLNK | 0o777)])
    with pytest.raises(UnsafeArchiveError):
        extractor.extract_zip_bytes(symlink_zip, destination_dir=tmp_path / "ws3")

    backslash_zip = _make_zip([("repo-main\\..\\secret.txt", b"bad", 0o100644)])
    with pytest.raises(UnsafeArchiveError):
        extractor.extract_zip_bytes(backslash_zip, destination_dir=tmp_path / "ws4")


def test_blocks_oversized_archives_and_strips_root_wrapper_and_git_folder(tmp_path: Path) -> None:
    settings = build_test_settings(
        tmp_path,
        WORKSPACE_MAX_ARCHIVE_BYTES=4096,
        WORKSPACE_MAX_EXTRACT_FILES=3,
        WORKSPACE_MAX_EXTRACT_BYTES=4096,
    )
    extractor = ArchiveExtractor(settings)

    too_many_files = _make_zip(
        [(f"repo-main/f{i}.py", b"x = 1\n", 0o100644) for i in range(4)]
    )
    with pytest.raises(UnsafeArchiveError):
        extractor.extract_zip_bytes(too_many_files, destination_dir=tmp_path / "ws_many")

    clean_zip = _make_zip(
        [
            ("owner-repo-123/.git/config", b"[core]\n", 0o100644),
            ("owner-repo-123/src/app.py", b"print('safe')\n", 0o100644),
            ("owner-repo-123/README.md", b"# Safe\n", 0o100644),
        ]
    )
    dest = tmp_path / "ws_clean"
    summary = extractor.extract_zip_bytes(clean_zip, destination_dir=dest)
    assert summary.file_count == 2
    assert (dest / "src" / "app.py").exists()
    assert (dest / "README.md").exists()
    assert not (dest / ".git").exists()
