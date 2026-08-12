"""Credential-leak detection for `POST /memories` (plan_v2.md SS9).

`remember()` must reject content that looks like a real secret and point the caller
at the `secret://...` reference convention instead of storing the value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CredentialPattern:
    name: str
    regex: re.Pattern[str]
    hint: str


CREDENTIAL_PATTERNS: list[CredentialPattern] = [
    CredentialPattern(
        "aws_access_key",
        re.compile(r"AKIA[A-Z0-9]{16}"),
        "AWS access key",
    ),
    CredentialPattern(
        "github_token",
        re.compile(r"ghp_[A-Za-z0-9]{20,}"),
        "GitHub personal access token",
    ),
    CredentialPattern(
        "generic_api_key",
        re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
        "API key (sk-... style)",
    ),
    CredentialPattern(
        "slack_token",
        re.compile(r"xox[bp]-[A-Za-z0-9-]+"),
        "Slack token",
    ),
    CredentialPattern(
        "password_assignment",
        # negative lookahead on `//` so the sanctioned `secret://...` reference format
        # (plan_v2.md SS9) is never itself flagged as a leak.
        re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key)\s*[:=](?!//)\s*\S+"),
        "inline password/secret assignment",
    ),
    CredentialPattern(
        "connection_string_with_password",
        re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^:@/\s]+:[^@/\s]+@[^\s/]+"),
        "connection string with embedded credentials",
    ),
]


def find_credential_leak(text: str) -> CredentialPattern | None:
    """Return the first matching credential pattern, or None if the text looks clean."""
    for pattern in CREDENTIAL_PATTERNS:
        if pattern.regex.search(text):
            return pattern
    return None


def credential_rejection_message(pattern: CredentialPattern) -> str:
    return (
        f"Contenido rechazado: parece contener un secreto real ({pattern.hint}). "
        "cerebro-memory nunca almacena valores de credenciales. Guarda una referencia en su "
        "lugar, por ejemplo: secret://<entorno>/<nombre> "
        "(ej. secret://production/aws-access-key)."
    )
