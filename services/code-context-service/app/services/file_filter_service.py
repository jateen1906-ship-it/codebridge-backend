"""Deterministic repository path filtering, language mapping, and secret detection."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

from app.core.config import Settings
from app.core.security import validate_safe_repo_path

_EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        ".virtualenv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".next",
        ".nuxt",
        ".output",
        "dist",
        "build",
        "coverage",
        "htmlcov",
        ".idea",
        ".vscode",
        ".terraform",
        "chroma_data",
        "target",
        "vendor",
        "bin",
        "obj",
        "out",
    }
)

_SAFE_ENV_FILENAMES = frozenset(
    {
        ".env.example",
        ".env.sample",
        ".env.template",
        "env.example",
    }
)

_SENSITIVE_EXACT_FILENAMES = frozenset(
    {
        ".env",
        ".envrc",
        ".npmrc",
        ".pypirc",
        ".netrc",
        ".htpasswd",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        "id_dsa",
        "credentials.json",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
    }
)

_SENSITIVE_EXTENSIONS = frozenset(
    {
        ".pem",
        ".key",
        ".p12",
        ".pfx",
        ".pkcs12",
        ".jks",
        ".keystore",
    }
)

_LOCKFILES_AND_ARTIFACTS = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "pipfile.lock",
        "cargo.lock",
        "composer.lock",
        "go.sum",
    }
)

_BINARY_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".bmp",
        ".tiff",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".o",
        ".a",
        ".lib",
        ".pyc",
        ".pyo",
        ".class",
        ".jar",
        ".war",
        ".whl",
        ".wasm",
        ".sqlite",
        ".db",
        ".ttf",
        ".otf",
        ".woff",
        ".woff2",
        ".eot",
        ".mp3",
        ".mp4",
        ".wav",
        ".avi",
        ".mov",
    }
)

_EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".cs": "csharp",
    ".php": "php",
    ".rb": "ruby",
    ".rs": "rust",
    ".html": "html",
    ".css": "css",
    ".scss": "css",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".sql": "sql",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
}

_SECRET_CONTENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key_block",
        re.compile(
            r"-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+|PGP\s+)?PRIVATE\s+KEY-----"
        ),
    ),
    (
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    ),
    (
        "aws_access_key",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    ),
    (
        "api_secret_key",
        re.compile(r"\bsk-(?:live-|proj-|ant-)?[A-Za-z0-9\-_]{20,}\b"),
    ),
    (
        "slack_token",
        re.compile(r"\bxox[baprs]-[0-9]{10,}-[A-Za-z0-9\-]{10,}\b"),
    ),
)


@dataclass(frozen=True, slots=True)
class FileFilterDecision:
    """Result of evaluating a repository path against safety & indexing rules."""

    allowed: bool
    normalized_path: str
    file_name: str
    extension: str
    language: str | None
    skip_reason: str | None = None


def is_excluded_directory(dir_path: str) -> bool:
    """Return True if any segment in the directory path is excluded from traversal."""
    normalized = validate_safe_repo_path(dir_path)
    if not normalized:
        return False
    segments = [seg.lower() for seg in normalized.split("/")]
    return any(seg in _EXCLUDED_DIR_NAMES or seg.endswith(".egg-info") for seg in segments)


def is_sensitive_filename(file_name: str) -> bool:
    """Return True if filename matches known credential, key, or private env files."""
    lower = file_name.lower()
    if lower in _SAFE_ENV_FILENAMES:
        return False
    if lower in _SENSITIVE_EXACT_FILENAMES:
        return True
    if lower.startswith(".env."):
        return True
    if "service-account" in lower and lower.endswith(".json"):
        return True
    if "firebase-adminsdk" in lower and lower.endswith(".json"):
        return True
    _, ext = posixpath.splitext(lower)
    if ext in _SENSITIVE_EXTENSIONS:
        return True
    return False


def detect_secret_in_content(text: str) -> str | None:
    """Detect high-confidence embedded credentials or private keys in file contents."""
    for label, pattern in _SECRET_CONTENT_PATTERNS:
        if pattern.search(text):
            return label
    return None


class FileFilterService:
    """Configurable path, extension, artifact, and secret filter for repo ingestion."""

    def __init__(self, settings: Settings) -> None:
        self._supported_extensions = settings.supported_extension_set
        self._max_file_bytes = settings.MAX_FILE_BYTES

    def evaluate_file(self, raw_path: str, *, declared_size: int | None = None) -> FileFilterDecision:
        """Evaluate whether a discovered repository file should be fetched and indexed."""
        normalized = validate_safe_repo_path(raw_path)
        file_name = posixpath.basename(normalized)
        lower_name = file_name.lower()
        _, ext = posixpath.splitext(lower_name)
        language = _EXTENSION_TO_LANGUAGE.get(ext)

        dir_part = posixpath.dirname(normalized)
        if dir_part and is_excluded_directory(dir_part):
            return FileFilterDecision(
                allowed=False,
                normalized_path=normalized,
                file_name=file_name,
                extension=ext,
                language=language,
                skip_reason="excluded_directory",
            )

        if is_sensitive_filename(file_name):
            return FileFilterDecision(
                allowed=False,
                normalized_path=normalized,
                file_name=file_name,
                extension=ext,
                language=language,
                skip_reason="sensitive_file",
            )

        if (
            lower_name in _LOCKFILES_AND_ARTIFACTS
            or lower_name.endswith((".min.js", ".min.css", ".bundle.js", ".map"))
        ):
            return FileFilterDecision(
                allowed=False,
                normalized_path=normalized,
                file_name=file_name,
                extension=ext,
                language=language,
                skip_reason="lockfile_or_artifact",
            )

        if ext in _BINARY_EXTENSIONS:
            return FileFilterDecision(
                allowed=False,
                normalized_path=normalized,
                file_name=file_name,
                extension=ext,
                language=language,
                skip_reason="binary_file",
            )

        if declared_size is not None and declared_size > self._max_file_bytes:
            return FileFilterDecision(
                allowed=False,
                normalized_path=normalized,
                file_name=file_name,
                extension=ext,
                language=language,
                skip_reason="oversized_file",
            )

        if not ext or ext not in self._supported_extensions:
            return FileFilterDecision(
                allowed=False,
                normalized_path=normalized,
                file_name=file_name,
                extension=ext,
                language=language,
                skip_reason="unsupported_extension",
            )

        return FileFilterDecision(
            allowed=True,
            normalized_path=normalized,
            file_name=file_name,
            extension=ext,
            language=language or "text",
            skip_reason=None,
        )

