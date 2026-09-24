"""Tests for token auth (`cerebro_memory.auth`), now backed by the shared
`cerebro_auth` schema (unified authentication across cerebro-memory/cerebro-docs/
cerebro-flows).

Token *management* (`POST/GET /tokens`, `DELETE /tokens/{name}`) moved entirely to
the new `cerebro-auth` service -- this file no longer exercises it. Instead, tests
that need a non-root token insert directly into `cerebro_auth.users`/`api_tokens`/
`user_groups`/`group_scopes` via raw SQL (see the `_create_*` helpers below), the way
`cerebro-auth` itself would populate those tables.

`TestPrincipal*` and `test_hash_token_*`/`test_generate_token_*` below are unit tests
over pure functions/dataclasses (no I/O), same spirit as tests/test_rrf.py and
tests/test_context_engine.py.

Everything else needs real Postgres behavior (the `cerebro_auth` schema, the full
FastAPI auth dependency chain) and skips automatically if DATABASE_URL is not
reachable - same pattern as tests/test_supersedence.py and tests/test_graph.py.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient

from cerebro_memory.api import create_app
from cerebro_memory.auth import Principal, generate_token, hash_token
from cerebro_memory.config import get_settings

# --------------------------------------------------------------------------- unit: Principal


def _principal(
    *,
    name="x",
    scopes=frozenset({"read"}),
    allowed_contexts=None,
    access_level="user",
    user_id=None,
    owner_filter=None,
    is_root=False,
) -> Principal:
    return Principal(
        name=name,
        scopes=scopes,
        allowed_contexts=allowed_contexts,
        access_level=access_level,
        user_id=user_id,
        owner_filter=owner_filter,
        is_root=is_root,
    )


def test_principal_has_scope():
    p = _principal(scopes=frozenset({"read", "write"}))
    assert p.has_scope("read")
    assert p.has_scope("write")
    assert not p.has_scope("admin")


def test_principal_context_allowed_none_means_all():
    p = _principal(allowed_contexts=None)
    assert p.context_allowed("anything")
    assert p.context_allowed(None)


def test_principal_context_allowed_restricted():
    p = _principal(allowed_contexts=frozenset({"salud", "aprendizaje"}))
    assert p.context_allowed("salud")
    assert not p.context_allowed("finanzas-personales")
    # None (no target context yet, e.g. context not decided) is never itself a denial
    assert p.context_allowed(None)


def test_principal_filter_slugs_unrestricted_is_identity():
    p = _principal(allowed_contexts=None)
    slugs = ["a", "b", "c"]
    assert p.filter_slugs(slugs) == slugs


def test_principal_filter_slugs_restricted():
    p = _principal(allowed_contexts=frozenset({"a", "c"}))
    assert p.filter_slugs(["a", "b", "c", "d"]) == ["a", "c"]


def test_principal_root_has_every_scope_and_no_restrictions():
    p = _principal(
        name="root",
        scopes=frozenset({"read", "write", "admin"}),
        allowed_contexts=None,
        access_level="admin",
        user_id=None,
        owner_filter=None,
        is_root=True,
    )
    assert p.is_root
    for scope in ("read", "write", "admin"):
        assert p.has_scope(scope)
    assert p.context_allowed("literalmente-cualquier-cosa")
    assert p.owner_filter is None


# --------------------------------------------------------------------------- unit: token helpers


def test_hash_token_is_deterministic_sha256_hex():
    h1 = hash_token("kos_abc123")
    h2 = hash_token("kos_abc123")
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex digest length
    assert all(c in "0123456789abcdef" for c in h1)


def test_hash_token_differs_for_different_input():
    assert hash_token("kos_a") != hash_token("kos_b")


def test_generate_token_has_prefix_and_is_unique():
    t1 = generate_token()
    t2 = generate_token()
    assert t1.startswith("kos_")
    assert t2.startswith("kos_")
    assert t1 != t2
    assert len(t1) > len("kos_") + 20  # comfortably long, not a placeholder


# --------------------------------------------------------------------------- integration


def _db_reachable(dsn: str) -> bool:
    async def _check() -> None:
        conn = await asyncpg.connect(dsn=dsn, timeout=8)
        await conn.close()

    try:
        asyncio.run(_check())
        return True
    except Exception:
        return False


def _run_sql(coro_fn, *args, **kwargs):
    """Run one async DB operation against the (already-migrated) test database in its
    own connection + its own event loop -- mirrors `_db_reachable` above. Each call
    gets a fresh connection so these helpers are safe to call from ordinary sync test
    functions any number of times."""

    async def _wrapper():
        conn = await asyncpg.connect(dsn=get_settings().database_url, timeout=8)
        try:
            return await coro_fn(conn, *args, **kwargs)
        finally:
            await conn.close()

    return asyncio.run(_wrapper())


async def _do_create_user(conn, *, access_level: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO cerebro_auth.users (id, name, email, access_level, password_hash)
        VALUES ($1, $2, $3, $4, $5)
        """,
        user_id,
        f"test-user-{user_id.hex[:8]}",
        f"{user_id.hex[:8]}@test.local",
        access_level,
        "x",  # not a real password hash - never authenticated through this path
    )
    return user_id


async def _do_create_group(conn) -> uuid.UUID:
    group_id = uuid.uuid4()
    slug = f"test-group-{group_id.hex[:8]}"
    await conn.execute(
        "INSERT INTO cerebro_auth.groups (id, slug, name) VALUES ($1, $2, $3)", group_id, slug, slug
    )
    return group_id


async def _do_add_to_group(conn, user_id: uuid.UUID, group_id: uuid.UUID) -> None:
    await conn.execute(
        "INSERT INTO cerebro_auth.user_groups (user_id, group_id) VALUES ($1, $2)", user_id, group_id
    )


async def _do_set_group_scopes(
    conn, group_id: uuid.UUID, *, allowed_modules: list[str] | None, module_scopes: dict | None
) -> None:
    await conn.execute(
        """
        INSERT INTO cerebro_auth.group_scopes (group_id, allowed_modules, module_scopes)
        VALUES ($1, $2, $3::jsonb)
        ON CONFLICT (group_id) DO UPDATE
        SET allowed_modules = EXCLUDED.allowed_modules, module_scopes = EXCLUDED.module_scopes
        """,
        group_id,
        allowed_modules,
        json.dumps(module_scopes) if module_scopes is not None else "{}",
    )


async def _do_create_token(
    conn,
    *,
    user_id: uuid.UUID | None,
    scopes: list[str],
    access_level: str | None,
    allowed_modules: list[str] | None,
    module_scopes: dict | None,
    name: str | None,
) -> str:
    plaintext = generate_token()
    token_id = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO cerebro_auth.api_tokens
            (id, token_hash, name, user_id, scopes, access_level, allowed_modules, module_scopes)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
        """,
        token_id,
        hash_token(plaintext),
        name or f"test-token-{token_id.hex[:8]}",
        user_id,
        scopes,
        access_level,
        allowed_modules,
        json.dumps(module_scopes) if module_scopes is not None else "{}",
    )
    return plaintext


def _create_service_token(*, scopes: list[str], contexts: list[str] | None = None, name: str | None = None) -> str:
    """Direct replacement for the old `/tokens`-backed helper this file used to call:
    a service token (`user_id IS NULL`), scoped to the 'memory' module and optionally
    restricted to `contexts`. A service token always gets `owner_filter=None` (Change
    3: ownership restriction only applies to tokens tied to a `cerebro_auth` user) --
    exactly the pre-ownership semantics these tests used to exercise via the
    now-removed `POST /tokens`."""
    module_scopes = {"memory": {"contexts": contexts}} if contexts is not None else None
    return _run_sql(
        _do_create_token,
        user_id=None,
        scopes=scopes,
        access_level="user",
        allowed_modules=["memory"],
        module_scopes=module_scopes,
        name=name,
    )


def _create_user_token(
    *,
    access_level: str,
    scopes: list[str],
    group_id: uuid.UUID | None = None,
    contexts: list[str] | None = None,
    allowed_modules: list[str] | None = ("memory",),
) -> tuple[str, uuid.UUID]:
    """A `cerebro_auth` user (`access_level` in {"user", "owner", "admin"}) plus a
    token bound to it (`user_id` set -> the token's own `access_level` column stays
    NULL, per the resolution algorithm). Optionally joins `group_id`. `contexts`, if
    given, is set as the TOKEN's own `module_scopes.memory.contexts` -- for a "user"
    token that's the effective value directly (base case); for an "owner" token it
    narrows whatever the group(s) grant. Returns `(token, user_id)`."""
    user_id = _run_sql(_do_create_user, access_level=access_level)
    if group_id is not None:
        _run_sql(_do_add_to_group, user_id, group_id)
    module_scopes = {"memory": {"contexts": contexts}} if contexts is not None else None
    token = _run_sql(
        _do_create_token,
        user_id=user_id,
        scopes=scopes,
        access_level=None,
        allowed_modules=list(allowed_modules) if allowed_modules is not None else None,
        module_scopes=module_scopes,
        name=None,
    )
    return token, user_id


@pytest.fixture(scope="module")
def client():
    settings = get_settings()
    if not _db_reachable(settings.database_url):
        pytest.skip("Postgres not reachable at DATABASE_URL - run `docker compose up -d` first")
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def root_headers():
    return {"Authorization": f"Bearer {get_settings().api_token}"}


def _make_context(client, root_headers) -> str:
    slug = f"test-auth-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/contexts",
        json={"slug": slug, "name": "Test auth context", "kind": "domain"},
        headers=root_headers,
    )
    assert resp.status_code == 201, resp.text
    return slug


def _make_memory(client, headers, slug: str, content: str, type_: str = "semantic") -> str:
    resp = client.post(
        "/memories",
        json={"content": content, "context": slug, "type": type_},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


class TestRootTokenCompat:
    def test_root_token_still_works_for_every_scope(self, client, root_headers):
        resp = client.get("/contexts", headers=root_headers)
        assert resp.status_code == 200, resp.text

    def test_missing_auth_header_is_401(self, client):
        resp = client.get("/contexts")
        assert resp.status_code == 401, resp.text

    def test_garbage_bearer_token_is_401(self, client):
        resp = client.get("/contexts", headers={"Authorization": "Bearer not-a-real-token"})
        assert resp.status_code == 401, resp.text


class TestModuleGate:
    """A token whose effective `allowed_modules` doesn't include 'memory' at all must
    never authenticate against this service (Change 1, step 2 - "specific to this
    service")."""

    def test_token_without_memory_module_is_rejected(self, client):
        token = _run_sql(
            _do_create_token,
            user_id=None,
            scopes=["read", "write"],
            access_level="user",
            allowed_modules=["docs", "flows"],  # no "memory"
            module_scopes=None,
            name=None,
        )
        resp = client.get("/contexts", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code in (401, 403), resp.text

    def test_revoked_token_is_401(self, client):
        async def _do(conn):
            plaintext = generate_token()
            await conn.execute(
                """
                INSERT INTO cerebro_auth.api_tokens
                    (id, token_hash, name, user_id, scopes, access_level, allowed_modules, module_scopes, revoked_at)
                VALUES ($1, $2, $3, NULL, $4, 'user', $5, '{}', now())
                """,
                uuid.uuid4(),
                hash_token(plaintext),
                f"test-token-revoked-{uuid.uuid4().hex[:8]}",
                ["read"],
                ["memory"],
            )
            return plaintext

        token = _run_sql(_do)
        resp = client.get("/contexts", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401, resp.text


class TestScopeEnforcement:
    def test_read_only_token_cannot_write(self, client, root_headers):
        slug = _make_context(client, root_headers)
        token = _create_service_token(scopes=["read"])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post(
            "/memories", json={"content": "algo", "context": slug, "type": "semantic"}, headers=headers
        )
        assert resp.status_code == 403, resp.text

    def test_write_only_token_cannot_read(self, client):
        token = _create_service_token(scopes=["write"])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get("/contexts", headers=headers)
        assert resp.status_code == 403, resp.text

    def test_export_requires_admin_scope_even_with_read_and_write(self, client):
        token = _create_service_token(scopes=["read", "write"])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get("/disambiguations/export", headers=headers)
        assert resp.status_code == 403, resp.text

    def test_token_name_overrides_x_agent_name_header_in_audit(self, client, root_headers):
        slug = _make_context(client, root_headers)
        name = f"test-token-identity-{uuid.uuid4().hex[:8]}"
        token = _create_service_token(scopes=["read", "write"], name=name)
        headers = {"Authorization": f"Bearer {token}", "X-Agent-Name": "someone-else-entirely"}

        resp = client.post(
            "/memories",
            json={"content": "a test memory", "context": slug, "type": "semantic"},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        # source defaults to the resolved agent identity when not given explicitly -
        # must be the token's real name, never the spoofed header.
        assert resp.json()["source"] == name


class TestAllowedContextsEnforcement:
    def test_write_outside_allowed_contexts_is_403(self, client, root_headers):
        allowed_slug = _make_context(client, root_headers)
        other_slug = _make_context(client, root_headers)
        token = _create_service_token(scopes=["read", "write"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        ok = client.post(
            "/memories", json={"content": "dentro", "context": allowed_slug, "type": "semantic"}, headers=headers
        )
        assert ok.status_code == 201, ok.text

        blocked = client.post(
            "/memories", json={"content": "fuera", "context": other_slug, "type": "semantic"}, headers=headers
        )
        assert blocked.status_code == 403, blocked.text

    def test_explicit_search_outside_allowed_contexts_is_403(self, client, root_headers):
        allowed_slug = _make_context(client, root_headers)
        other_slug = _make_context(client, root_headers)
        token = _create_service_token(scopes=["read"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get("/memories/search", params={"q": "x", "context": other_slug}, headers=headers)
        assert resp.status_code == 403, resp.text

    def test_scope_all_search_is_silently_narrowed_to_allowed_contexts(self, client, root_headers):
        allowed_slug = _make_context(client, root_headers)
        other_slug = _make_context(client, root_headers)

        client.post(
            "/memories",
            json={"content": "presupuesto mensual de la casa", "context": allowed_slug, "type": "semantic"},
            headers=root_headers,
        )
        client.post(
            "/memories",
            json={"content": "presupuesto mensual del proyecto", "context": other_slug, "type": "semantic"},
            headers=root_headers,
        )

        token = _create_service_token(scopes=["read"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get(
            "/memories/search", params={"q": "presupuesto mensual", "scope": "all", "limit": 10}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        contexts_seen = {r["context"] for r in resp.json()["results"]}
        assert contexts_seen <= {allowed_slug}

    def test_stats_filters_memories_by_context_to_allowed(self, client, root_headers):
        allowed_slug = _make_context(client, root_headers)
        other_slug = _make_context(client, root_headers)
        client.post(
            "/memories",
            json={"content": "algo en el contexto permitido", "context": allowed_slug, "type": "semantic"},
            headers=root_headers,
        )
        client.post(
            "/memories",
            json={"content": "algo en el contexto ajeno", "context": other_slug, "type": "semantic"},
            headers=root_headers,
        )

        token = _create_service_token(scopes=["read"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get("/stats", headers=headers)
        assert resp.status_code == 200, resp.text
        contexts_seen = {row["context"] for row in resp.json()["memories_by_context"]}
        assert other_slug not in contexts_seen


class TestDeleteContext:
    def test_delete_empty_context_succeeds_without_force(self, client, root_headers):
        slug = _make_context(client, root_headers)
        resp = client.delete(f"/contexts/{slug}", headers=root_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"slug": slug, "status": "deleted", "memories_deleted": 0}

        listed = client.get("/contexts", headers=root_headers)
        assert slug not in {c["slug"] for c in listed.json()}

    def test_delete_context_with_memories_is_409_without_force(self, client, root_headers):
        slug = _make_context(client, root_headers)
        client.post(
            "/memories", json={"content": "algo", "context": slug, "type": "semantic"}, headers=root_headers
        )
        resp = client.delete(f"/contexts/{slug}", headers=root_headers)
        assert resp.status_code == 409, resp.text

    def test_delete_context_with_force_hard_deletes_memories(self, client, root_headers):
        slug = _make_context(client, root_headers)
        client.post(
            "/memories", json={"content": "algo", "context": slug, "type": "semantic"}, headers=root_headers
        )
        resp = client.delete(f"/contexts/{slug}", params={"force": True}, headers=root_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["memories_deleted"] == 1

    def test_delete_nonexistent_context_is_404(self, client, root_headers):
        resp = client.delete(f"/contexts/does-not-exist-{uuid.uuid4().hex[:8]}", headers=root_headers)
        assert resp.status_code == 404, resp.text

    def test_delete_context_requires_admin_not_just_write(self, client, root_headers):
        slug = _make_context(client, root_headers)
        token = _create_service_token(scopes=["read", "write"])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.delete(f"/contexts/{slug}", headers=headers)
        assert resp.status_code == 403, resp.text


class TestAllowedContextsEnforcementOnRelated:
    """GET /memories/{id}/related must not leak memories or neighbors from a context
    outside a restricted token's allowlist - including neighbors reached via an
    EXPLICIT edge, which `cross_context` marks as an intentional bridge only for
    tokens that can see both sides, never as an allowlist bypass."""

    def test_related_on_memory_outside_allowed_contexts_is_403(self, client, root_headers):
        allowed_slug = _make_context(client, root_headers)
        other_slug = _make_context(client, root_headers)
        other_memory = _make_memory(client, root_headers, other_slug, "memoria en contexto ajeno")

        token = _create_service_token(scopes=["read"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get(f"/memories/{other_memory}/related", headers=headers)
        assert resp.status_code == 403, resp.text

    def test_related_on_nonexistent_memory_is_404_not_403(self, client, root_headers):
        allowed_slug = _make_context(client, root_headers)
        token = _create_service_token(scopes=["read"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get(f"/memories/{uuid.uuid4()}/related", headers=headers)
        assert resp.status_code == 404, resp.text

    def test_cross_context_neighbor_via_explicit_edge_is_hidden_from_restricted_token(
        self, client, root_headers
    ):
        allowed_slug = _make_context(client, root_headers)
        other_slug = _make_context(client, root_headers)
        anchor = _make_memory(client, root_headers, allowed_slug, "decision dentro del contexto permitido", "decision")
        foreign_neighbor = _make_memory(client, root_headers, other_slug, "causa en un contexto ajeno")

        edge_resp = client.post(
            f"/memories/{anchor}/edges",
            json={"to_memory": foreign_neighbor, "relation": "caused_by"},
            headers=root_headers,
        )
        assert edge_resp.status_code == 201, edge_resp.text

        # Root (unrestricted) sees the cross-context neighbor, marked as such.
        root_related = client.get(f"/memories/{anchor}/related", headers=root_headers)
        assert root_related.status_code == 200, root_related.text
        assert any(r["memory"]["id"] == foreign_neighbor for r in root_related.json()["related"])

        # A token restricted to allowed_slug must NOT see it at all - not even
        # marked cross_context - even though the edge is a real, explicit bridge.
        token = _create_service_token(scopes=["read"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get(f"/memories/{anchor}/related", headers=headers)
        assert resp.status_code == 200, resp.text
        neighbor_ids = {r["memory"]["id"] for r in resp.json()["related"]}
        assert foreign_neighbor not in neighbor_ids

    def test_search_expand_hides_cross_context_neighbor_from_restricted_token(self, client, root_headers):
        allowed_slug = _make_context(client, root_headers)
        other_slug = _make_context(client, root_headers)
        anchor = _make_memory(client, root_headers, allowed_slug, "presupuesto trimestral aprobado", "decision")
        foreign_neighbor = _make_memory(client, root_headers, other_slug, "motivo del presupuesto en otro contexto")

        edge_resp = client.post(
            f"/memories/{anchor}/edges",
            json={"to_memory": foreign_neighbor, "relation": "caused_by"},
            headers=root_headers,
        )
        assert edge_resp.status_code == 201, edge_resp.text

        token = _create_service_token(scopes=["read"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get(
            "/memories/search",
            params={"q": "presupuesto trimestral aprobado", "context": allowed_slug, "expand": True},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        related = resp.json().get("related") or []
        neighbor_ids = {r["memory"]["id"] for r in related}
        assert foreign_neighbor not in neighbor_ids


class TestAllowedContextsEnforcementOnEdges:
    """POST/DELETE .../edges must check BOTH endpoints of an edge against
    allowed_contexts, not just the path memory_id - the other side of an edge can sit
    in a different (disallowed) context."""

    def test_create_edge_is_403_when_to_memory_context_is_disallowed(self, client, root_headers):
        allowed_slug = _make_context(client, root_headers)
        other_slug = _make_context(client, root_headers)
        anchor = _make_memory(client, root_headers, allowed_slug, "memoria de origen")
        foreign_target = _make_memory(client, root_headers, other_slug, "memoria de destino ajena")

        token = _create_service_token(scopes=["read", "write"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post(
            f"/memories/{anchor}/edges",
            json={"to_memory": foreign_target, "relation": "relates_to"},
            headers=headers,
        )
        assert resp.status_code == 403, resp.text

    def test_create_edge_is_403_when_from_memory_context_is_disallowed(self, client, root_headers):
        allowed_slug = _make_context(client, root_headers)
        other_slug = _make_context(client, root_headers)
        foreign_anchor = _make_memory(client, root_headers, other_slug, "memoria de origen ajena")
        target = _make_memory(client, root_headers, allowed_slug, "memoria de destino")

        token = _create_service_token(scopes=["read", "write"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post(
            f"/memories/{foreign_anchor}/edges",
            json={"to_memory": target, "relation": "relates_to"},
            headers=headers,
        )
        assert resp.status_code == 403, resp.text

    def test_create_edge_succeeds_when_both_endpoints_allowed(self, client, root_headers):
        allowed_slug = _make_context(client, root_headers)
        a = _make_memory(client, root_headers, allowed_slug, "memoria a")
        b = _make_memory(client, root_headers, allowed_slug, "memoria b")

        token = _create_service_token(scopes=["read", "write"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post(
            f"/memories/{a}/edges", json={"to_memory": b, "relation": "relates_to"}, headers=headers
        )
        assert resp.status_code == 201, resp.text

    def test_delete_edge_is_403_when_other_endpoint_context_is_disallowed(self, client, root_headers):
        allowed_slug = _make_context(client, root_headers)
        other_slug = _make_context(client, root_headers)
        anchor = _make_memory(client, root_headers, allowed_slug, "memoria ancla")
        foreign_neighbor = _make_memory(client, root_headers, other_slug, "vecino ajeno")

        edge_resp = client.post(
            f"/memories/{anchor}/edges",
            json={"to_memory": foreign_neighbor, "relation": "relates_to"},
            headers=root_headers,
        )
        assert edge_resp.status_code == 201, edge_resp.text
        edge_id = edge_resp.json()["id"]

        token = _create_service_token(scopes=["read", "write"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        # memory_id in the path (anchor) IS allowed, but the edge's other endpoint
        # (foreign_neighbor) is not - must still be rejected.
        resp = client.delete(f"/memories/{anchor}/edges/{edge_id}", headers=headers)
        assert resp.status_code == 403, resp.text

        # the edge must survive the rejected attempt
        still_there = client.get(f"/memories/{anchor}/related", headers=root_headers)
        assert any(r["edge_id"] == edge_id for r in still_there.json()["related"])

    def test_delete_edge_succeeds_when_both_endpoints_allowed(self, client, root_headers):
        allowed_slug = _make_context(client, root_headers)
        a = _make_memory(client, root_headers, allowed_slug, "memoria a2")
        b = _make_memory(client, root_headers, allowed_slug, "memoria b2")
        edge_resp = client.post(
            f"/memories/{a}/edges", json={"to_memory": b, "relation": "relates_to"}, headers=root_headers
        )
        edge_id = edge_resp.json()["id"]

        token = _create_service_token(scopes=["read", "write"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.delete(f"/memories/{a}/edges/{edge_id}", headers=headers)
        assert resp.status_code == 200, resp.text


class TestOwnershipFilter:
    """Change 3: `owner_user_id` + `Principal.owner_filter` - a gate separate from
    and additional to the module/context gate above."""

    def test_user_level_token_only_sees_what_it_created(self, client):
        token_a, user_a = _create_user_token(access_level="user", scopes=["read", "write"])
        token_b, user_b = _create_user_token(access_level="user", scopes=["read", "write"])
        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}

        ctx_resp = client.post(
            "/contexts",
            json={"slug": f"test-owner-{uuid.uuid4().hex[:8]}", "name": "owner test", "kind": "domain"},
            headers=headers_a,
        )
        assert ctx_resp.status_code == 201, ctx_resp.text
        slug = ctx_resp.json()["slug"]

        mine = _make_memory(client, headers_a, slug, "memoria propia de user_a unica-xyz123")
        theirs = _make_memory(client, headers_b, slug, "memoria propia de user_b unica-xyz123")

        resp = client.get(
            "/memories/search",
            params={"q": "unica-xyz123", "context": slug, "limit": 10},
            headers=headers_a,
        )
        assert resp.status_code == 200, resp.text
        ids_seen = {r["id"] for r in resp.json()["results"]}
        assert mine in ids_seen
        assert theirs not in ids_seen

    def test_owner_level_token_sees_group_mates_content(self, client):
        group_id = _run_sql(_do_create_group)
        _run_sql(
            _do_set_group_scopes,
            group_id,
            allowed_modules=["memory"],
            module_scopes=None,  # no restriction within memory for this group
        )
        token_a, user_a = _create_user_token(access_level="owner", scopes=["read", "write"], group_id=group_id)
        token_b, user_b = _create_user_token(access_level="owner", scopes=["read", "write"], group_id=group_id)
        # an owner NOT in the group must not show up as a group-mate.
        token_c, user_c = _create_user_token(access_level="owner", scopes=["read", "write"])
        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}
        headers_c = {"Authorization": f"Bearer {token_c}"}

        ctx_resp = client.post(
            "/contexts",
            json={"slug": f"test-groupowner-{uuid.uuid4().hex[:8]}", "name": "group owner test", "kind": "domain"},
            headers=headers_a,
        )
        assert ctx_resp.status_code == 201, ctx_resp.text
        slug = ctx_resp.json()["slug"]

        mine = _make_memory(client, headers_a, slug, "memoria de user_a en grupo owner-test-abc999")
        mates = _make_memory(client, headers_b, slug, "memoria de user_b en grupo owner-test-abc999")
        outsider = _make_memory(client, headers_c, slug, "memoria de user_c fuera del grupo owner-test-abc999")

        resp = client.get(
            "/memories/search",
            params={"q": "owner-test-abc999", "context": slug, "limit": 10},
            headers=headers_a,
        )
        assert resp.status_code == 200, resp.text
        ids_seen = {r["id"] for r in resp.json()["results"]}
        assert mine in ids_seen
        assert mates in ids_seen
        assert outsider not in ids_seen

    def test_admin_and_root_see_everything_regardless_of_owner(self, client, root_headers):
        token_a, user_a = _create_user_token(access_level="user", scopes=["read", "write"])
        headers_a = {"Authorization": f"Bearer {token_a}"}

        ctx_resp = client.post(
            "/contexts",
            json={"slug": f"test-admin-owner-{uuid.uuid4().hex[:8]}", "name": "admin owner test", "kind": "domain"},
            headers=root_headers,
        )
        assert ctx_resp.status_code == 201, ctx_resp.text
        slug = ctx_resp.json()["slug"]

        someones = _make_memory(client, headers_a, slug, "memoria de user_a visible-para-admin-xyz")

        resp = client.get(
            "/memories/search",
            params={"q": "visible-para-admin-xyz", "context": slug, "limit": 10},
            headers=root_headers,
        )
        assert resp.status_code == 200, resp.text
        ids_seen = {r["id"] for r in resp.json()["results"]}
        assert someones in ids_seen

    def test_update_and_delete_respect_ownership(self, client):
        token_a, user_a = _create_user_token(access_level="user", scopes=["read", "write"])
        token_b, user_b = _create_user_token(access_level="user", scopes=["read", "write"])
        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}

        ctx_resp = client.post(
            "/contexts",
            json={"slug": f"test-owner-write-{uuid.uuid4().hex[:8]}", "name": "owner write test", "kind": "domain"},
            headers=headers_a,
        )
        assert ctx_resp.status_code == 201, ctx_resp.text
        slug = ctx_resp.json()["slug"]

        memory_id = _make_memory(client, headers_a, slug, "memoria mutable de user_a")

        patch_resp = client.patch(
            f"/memories/{memory_id}", json={"content": "editada por user_b"}, headers=headers_b
        )
        assert patch_resp.status_code == 403, patch_resp.text

        delete_resp = client.delete(f"/memories/{memory_id}", headers=headers_b)
        assert delete_resp.status_code == 403, delete_resp.text

        own_patch = client.patch(
            f"/memories/{memory_id}", json={"content": "editada por user_a"}, headers=headers_a
        )
        assert own_patch.status_code == 200, own_patch.text
