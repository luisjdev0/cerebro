"""Unit tests for credential-leak detection - no database needed."""

import pytest

from cerebro_memory.security import credential_rejection_message, find_credential_leak

LEAKY_EXAMPLES = [
    "mi AWS_SECRET=AKIAIOSFODNN7EXAMPLE",
    "AKIAIOSFODNN7EXAMPLE es mi access key",
    "token de github: ghp_1234567890abcdefghijklmnopqrstuvwx",
    "usa esta key sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
    "slack token xoxb-1234567890-abcdefghijklmnop",
    "password=hunter2 para el servidor",
    "connection string postgresql://user:supersecret@db.example.com:5432/prod",
]

CLEAN_EXAMPLES = [
    "El usuario prefiere que los reportes de gastos se generen los lunes.",
    "Referencia la credencial como secret://production/aws-access-key",
    "Recordar renovar el dominio en marzo.",
    "El proyecto expense-tracker usa FastAPI y Postgres.",
]


@pytest.mark.parametrize("text", LEAKY_EXAMPLES)
def test_detects_credential_leak(text):
    match = find_credential_leak(text)
    assert match is not None, f"expected a credential match in: {text!r}"


@pytest.mark.parametrize("text", CLEAN_EXAMPLES)
def test_does_not_flag_clean_text(text):
    assert find_credential_leak(text) is None


def test_rejection_message_points_at_secret_reference_format():
    match = find_credential_leak("mi AWS_SECRET=AKIAIOSFODNN7EXAMPLE")
    assert match is not None
    message = credential_rejection_message(match)
    assert "secret://" in message
