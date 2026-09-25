"""`AuthClient` must route each method to the correct endpoint/verb/body of the
cerebro-auth API -- same criterion as test_flows_client.py. `login()` gets its own
class: it must NOT send this client's own `Authorization` header (see
`AuthClient.login`/`BaseClient._request`)."""

from __future__ import annotations

import pytest

from cerebro_clients.auth_client import AuthClient
from cerebro_clients.base import CerebroAPIError, CerebroConnectionError

from ._helpers import BytesTransport, RaisingTransport, RecordingTransport


def make_client(transport, **kwargs) -> AuthClient:
    return AuthClient(base_url="http://test-auth", token="tok-abc", agent="test-agent", transport=transport, **kwargs)


def test_auth_and_agent_headers_are_set():
    transport = RecordingTransport()
    make_client(transport).list_users()
    headers = transport.last["headers"]
    assert headers["authorization"] == "Bearer tok-abc"
    assert headers["x-agent-name"] == "test-agent"


class TestUsers:
    def test_create_user_minimal(self):
        transport = RecordingTransport()
        make_client(transport).create_user("Jose")
        assert transport.last["method"] == "POST"
        assert transport.last["path"] == "/users"
        assert transport.last["json"] == {"name": "Jose", "access_level": "user"}

    def test_create_user_with_email_and_access_level(self):
        transport = RecordingTransport()
        make_client(transport).create_user("Jose", email="jose@example.com", access_level="admin")
        assert transport.last["json"] == {
            "name": "Jose",
            "access_level": "admin",
            "email": "jose@example.com",
        }

    def test_list_users(self):
        transport = RecordingTransport(response_json=[])
        make_client(transport).list_users()
        assert transport.last["method"] == "GET"
        assert transport.last["path"] == "/users"


class TestGroups:
    def test_create_group(self):
        transport = RecordingTransport()
        make_client(transport).create_group("eng", "Engineering")
        assert transport.last["method"] == "POST"
        assert transport.last["path"] == "/groups"
        assert transport.last["json"] == {"slug": "eng", "name": "Engineering"}

    def test_set_group_scopes_minimal(self):
        transport = RecordingTransport()
        make_client(transport).set_group_scopes("eng", ["memory", "docs"])
        assert transport.last["method"] == "POST"
        assert transport.last["path"] == "/groups/eng/scopes"
        assert transport.last["json"] == {"allowed_modules": ["memory", "docs"]}

    def test_set_group_scopes_with_module_scopes(self):
        transport = RecordingTransport()
        make_client(transport).set_group_scopes("eng", ["memory"], module_scopes={"memory": ["read"]})
        assert transport.last["json"] == {
            "allowed_modules": ["memory"],
            "module_scopes": {"memory": ["read"]},
        }

    def test_add_group_member(self):
        transport = RecordingTransport()
        make_client(transport).add_group_member("eng", "jose")
        assert transport.last["method"] == "POST"
        assert transport.last["path"] == "/groups/eng/members"
        assert transport.last["json"] == {"user": "jose"}


class TestTokens:
    def test_create_token_minimal(self):
        transport = RecordingTransport()
        make_client(transport).create_token("agente-x", ["read"])
        assert transport.last["method"] == "POST"
        assert transport.last["path"] == "/tokens"
        assert transport.last["json"] == {"name": "agente-x", "scopes": ["read"]}

    def test_create_token_full(self):
        transport = RecordingTransport()
        make_client(transport).create_token(
            "agente-x",
            ["read", "write"],
            allowed_modules=["memory"],
            module_scopes={"memory": ["read"]},
            user="jose",
            access_level="admin",
            value="cauth_provided-secret",
        )
        assert transport.last["json"] == {
            "name": "agente-x",
            "scopes": ["read", "write"],
            "allowed_modules": ["memory"],
            "module_scopes": {"memory": ["read"]},
            "user": "jose",
            "access_level": "admin",
            "value": "cauth_provided-secret",
        }

    def test_list_tokens(self):
        transport = RecordingTransport(response_json=[])
        make_client(transport).list_tokens()
        assert transport.last["method"] == "GET"
        assert transport.last["path"] == "/tokens"

    def test_revoke_token(self):
        transport = RecordingTransport()
        make_client(transport).revoke_token("agente-x")
        assert transport.last["method"] == "DELETE"
        assert transport.last["path"] == "/tokens/agente-x"


class TestBackup:
    def test_streams_response_body_to_dest_file(self, tmp_path):
        transport = BytesTransport(content=b"-- pg_dump output --")
        dest = tmp_path / "dump.sql"

        make_client(transport).backup(dest)

        assert transport.last["method"] == "POST"
        assert transport.last["path"] == "/backup"
        assert dest.read_bytes() == b"-- pg_dump output --"

    def test_sends_this_client_own_auth_header(self, tmp_path):
        transport = BytesTransport(content=b"data")
        make_client(transport).backup(tmp_path / "dump.sql")
        assert transport.last["headers"]["authorization"] == "Bearer tok-abc"

    def test_error_response_raises_and_writes_no_file(self, tmp_path):
        transport = BytesTransport(status_code=403, detail_json={"detail": "admin access required"})
        dest = tmp_path / "dump.sql"

        with pytest.raises(CerebroAPIError) as exc_info:
            make_client(transport).backup(dest)

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "admin access required"
        assert not dest.exists()

    def test_connection_failure_raises_cerebro_connection_error(self, tmp_path):
        client = make_client(RaisingTransport())
        with pytest.raises(CerebroConnectionError):
            client.backup(tmp_path / "dump.sql")


class TestLogin:
    def test_login_sends_token_in_body(self):
        transport = RecordingTransport(response_json={"name": "agente-x", "access_level": "admin", "allowed_modules": []})
        result = make_client(transport).login("token-being-checked")
        assert transport.last["method"] == "POST"
        assert transport.last["path"] == "/login"
        assert transport.last["json"] == {"token": "token-being-checked"}
        assert result == {"name": "agente-x", "access_level": "admin", "allowed_modules": []}

    def test_login_does_not_send_this_client_own_auth_header(self):
        # The client was built with its own token ("tok-abc", see make_client), but
        # login() validates a *different* token given as an argument -- it must not
        # leak the client's own Authorization header on this one request.
        transport = RecordingTransport(response_json={"name": "x", "access_level": "user", "allowed_modules": []})
        make_client(transport).login("some-other-token")
        headers = transport.last["headers"]
        assert "authorization" not in headers
        # the agent header is unrelated to auth and is still expected to go through
        assert headers["x-agent-name"] == "test-agent"

    def test_login_failure_raises_cerebro_api_error(self):
        transport = RecordingTransport(response_json={"detail": "invalid token"}, status_code=401)
        client = make_client(transport)
        with pytest.raises(CerebroAPIError) as exc_info:
            client.login("bad-token")
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "invalid token"


class TestErrorPropagation:
    def test_http_error_raises_cerebro_api_error(self):
        transport = RecordingTransport(response_json={"detail": "user not found"}, status_code=404)
        client = make_client(transport)
        with pytest.raises(CerebroAPIError) as exc_info:
            client.revoke_token("no-existe")
        assert exc_info.value.status_code == 404

    def test_connection_failure_raises_cerebro_connection_error(self):
        client = make_client(RaisingTransport())
        with pytest.raises(CerebroConnectionError):
            client.list_users()

    def test_connection_failure_on_login_raises_cerebro_connection_error(self):
        client = make_client(RaisingTransport())
        with pytest.raises(CerebroConnectionError):
            client.login("some-token")
