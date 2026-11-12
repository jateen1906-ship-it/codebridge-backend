"""Secret filename and embedded credential content detection tests."""

from __future__ import annotations

from app.clients.github_client import GitHubContentNode
from app.services.file_filter_service import (
    FileFilterService,
    detect_secret_in_content,
    is_sensitive_filename,
)
from app.services.file_parser_service import FileParserService
from tests.conftest import PROJECT_ID


def test_sensitive_filenames_blocked_while_safe_templates_allowed(base_test_settings) -> None:
    svc = FileFilterService(base_test_settings)
    for sensitive in (
        ".env",
        ".env.production",
        ".env.local",
        "keys/server.pem",
        "certs/private.key",
        "secrets/id_rsa",
        "config/credentials.json",
        "firebase-adminsdk-prod.json",
    ):
        assert is_sensitive_filename(sensitive.rsplit("/", 1)[-1]) is True
        decision = svc.evaluate_file(sensitive, declared_size=128)
        assert decision.allowed is False
        assert decision.skip_reason == "sensitive_file"

    # Safe templates like .env.example remain non-sensitive filenames
    assert is_sensitive_filename(".env.example") is False
    assert is_sensitive_filename(".env.sample") is False


def test_secret_content_scanner_blocks_embedded_credentials(base_test_settings) -> None:
    parser = FileParserService(base_test_settings)
    samples = [
        "PRIVATE = '-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAK'\n",
        "GH_TOKEN = 'ghp_1234567890abcdefghijklmnopqrstuvwxyzABCD'\n",
        "AWS_KEY = 'AKIAIOSFODNN7EXAMPLE'\n",
        "OPENAI_KEY = 'sk-proj-abcdefghijklmnopqrstuvwxyz123456'\n",
    ]
    for content in samples:
        assert detect_secret_in_content(content) is not None
        node = GitHubContentNode(
            project_id=PROJECT_ID,
            full_name="owner/repo",
            ref="main",
            path="app/config.py",
            kind="file",
            size=len(content.encode()),
            encoding="utf-8",
            content=content,
        )
        parsed = parser.parse(node)
        assert parsed.accepted is False
        assert parsed.skip_reason == "secret_detected"


def test_normal_placeholder_code_not_falsely_flagged(base_test_settings) -> None:
    normal_code = (
        "import os\n"
        "API_KEY = os.environ.get('PAYMENT_API_KEY', 'change_me_local_only')\n"
    )
    assert detect_secret_in_content(normal_code) is None

