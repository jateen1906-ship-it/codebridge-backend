"""Secure zip archive validation and path-traversal-proof extraction."""

from __future__ import annotations

import hashlib
import io
import os
import posixpath
import shutil
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.core.exceptions import UnsafeArchiveError


@dataclass(frozen=True, slots=True)
class ExtractedSnapshotSummary:
    file_count: int
    total_bytes: int
    archive_sha256: str


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    """Detect POSIX symlink mode bits stored in ZIP external_attr."""
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode)


def _is_special_file(info: zipfile.ZipInfo) -> bool:
    """Detect FIFO, socket, or device special bits in ZIP external_attr."""
    mode = info.external_attr >> 16
    if mode == 0:
        return False
    return stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)


def _strip_github_root_prefix(raw_names: list[str]) -> bool:
    """Return True if all non-empty entries share a single GitHub zipball top directory."""
    top_dirs: set[str] = set()
    for name in raw_names:
        cleaned = name.strip("/")
        if not cleaned:
            continue
        top_dirs.add(cleaned.split("/", 1)[0])
        if len(top_dirs) > 1:
            return False
    return len(top_dirs) == 1 and any("/" in n.strip("/") for n in raw_names if n.strip("/"))


def _validate_and_normalize_member_path(raw_name: str, strip_root: bool) -> str | None:
    """Validate zip member path, blocking traversal, drive letters, backslashes, and .git."""
    if not raw_name or "\x00" in raw_name or "\\" in raw_name:
        raise UnsafeArchiveError("Archive contains illegal path characters.")
    if raw_name.startswith("/") or (len(raw_name) >= 2 and raw_name[1] == ":"):
        raise UnsafeArchiveError("Archive contains absolute or drive-prefixed paths.")

    raw_parts = [p for p in raw_name.split("/") if p]
    if any(p in {".", ".."} for p in raw_parts):
        raise UnsafeArchiveError("Archive contains unsafe path traversal segments.")

    normalized = posixpath.normpath(raw_name)
    if normalized in {".", ".."} or normalized.startswith("../") or "/../" in normalized:
        raise UnsafeArchiveError("Archive contains path traversal sequences.")

    parts = [p for p in normalized.split("/") if p]
    if not parts:
        return None
    if any(p in {".", ".."} for p in parts):
        raise UnsafeArchiveError("Archive contains unsafe relative segments.")

    if strip_root:
        parts = parts[1:]
        if not parts:
            return None

    if parts[0] == ".git" or ".git" in parts:
        return None

    return "/".join(parts)


class ArchiveExtractor:
    """Validates bounded GitHub snapshot zips and safely extracts them into workspace storage."""

    def __init__(self, settings: Settings) -> None:
        self._max_archive_bytes = settings.WORKSPACE_MAX_ARCHIVE_BYTES
        self._max_extract_bytes = settings.WORKSPACE_MAX_EXTRACT_BYTES
        self._max_extract_files = settings.WORKSPACE_MAX_EXTRACT_FILES
        self._max_ratio = settings.WORKSPACE_MAX_COMPRESSION_RATIO

    def extract_zip_bytes(
        self,
        archive_bytes: bytes,
        *,
        destination_dir: Path,
    ) -> ExtractedSnapshotSummary:
        """Validate archive thoroughly before writing files cleanly under destination_dir."""
        if not archive_bytes:
            raise UnsafeArchiveError("Archive payload is empty.")
        if len(archive_bytes) > self._max_archive_bytes:
            raise UnsafeArchiveError(
                f"Compressed archive exceeds {self._max_archive_bytes} bytes limit."
            )

        archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()

        try:
            zf = zipfile.ZipFile(io.BytesIO(archive_bytes), "r")
        except zipfile.BadZipFile as exc:
            raise UnsafeArchiveError("Repository snapshot is not a valid zip archive.") from exc

        validated_entries: list[tuple[zipfile.ZipInfo, str]] = []
        total_uncompressed = 0
        total_compressed = 0

        with zf:
            infos = zf.infolist()
            if len(infos) > self._max_extract_files * 2:
                raise UnsafeArchiveError("Archive contains too many zip directory entries.")

            raw_names = [info.filename for info in infos if info.filename]
            strip_root = _strip_github_root_prefix(raw_names)

            for info in infos:
                if _is_symlink(info):
                    raise UnsafeArchiveError("Symbolic links inside repository archives are forbidden.")
                if _is_special_file(info):
                    raise UnsafeArchiveError("Special device/FIFO files inside archives are forbidden.")
                if info.is_dir():
                    _validate_and_normalize_member_path(info.filename, strip_root=False)
                    continue

                rel_path = _validate_and_normalize_member_path(
                    info.filename,
                    strip_root=strip_root,
                )
                if rel_path is None:
                    continue

                if info.file_size < 0:
                    raise UnsafeArchiveError("Archive entry reports invalid negative size.")
                total_uncompressed += info.file_size
                total_compressed += max(1, info.compress_size)

                if len(validated_entries) + 1 > self._max_extract_files:
                    raise UnsafeArchiveError(
                        f"Archive exceeds maximum extracted file count ({self._max_extract_files})."
                    )
                if total_uncompressed > self._max_extract_bytes:
                    raise UnsafeArchiveError(
                        f"Archive uncompressed size exceeds {self._max_extract_bytes} bytes."
                    )

                ratio = info.file_size / max(1, info.compress_size)
                if info.file_size > 1_048_576 and ratio > self._max_ratio:
                    raise UnsafeArchiveError("Archive triggered compression-ratio zip bomb protection.")

                validated_entries.append((info, rel_path))

            overall_ratio = total_uncompressed / max(1, total_compressed)
            if total_uncompressed > 1_048_576 and overall_ratio > self._max_ratio:
                raise UnsafeArchiveError("Archive triggered overall compression-ratio protection.")

            resolved_root = destination_dir.resolve()
            resolved_root.mkdir(parents=True, exist_ok=True)

            actual_bytes = 0
            file_count = 0
            for info, rel_path in validated_entries:
                target_path = (resolved_root / rel_path).resolve()
                if not target_path.is_relative_to(resolved_root):
                    raise UnsafeArchiveError("Extracted path escaped target workspace directory.")
                target_path.parent.mkdir(parents=True, exist_ok=True)

                with zf.open(info, "r") as src, open(target_path, "wb") as dst:
                    while True:
                        chunk = src.read(65536)
                        if not chunk:
                            break
                        actual_bytes += len(chunk)
                        if actual_bytes > self._max_extract_bytes:
                            raise UnsafeArchiveError(
                                "Extracted content exceeded byte limit while streaming."
                            )
                        dst.write(chunk)
                file_count += 1

        return ExtractedSnapshotSummary(
            file_count=file_count,
            total_bytes=actual_bytes,
            archive_sha256=archive_sha256,
        )

    @staticmethod
    def clear_directory_contents(directory: Path) -> None:
        """Safely remove all files inside an isolated workspace directory."""
        if not directory.exists():
            return
        for child in directory.iterdir():
            if child.is_symlink() or child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                shutil.rmtree(child, ignore_errors=False)

    @staticmethod
    def remove_workspace_directory(directory: Path) -> None:
        """Remove an entire workspace directory safely."""
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=False)
