"""Regresion del endurecimiento `extra="forbid"` en los modelos de entrada (StrictIn).

Sin el forbid, un typo del cliente en el nombre de un campo (p.ej. `content` en vez
de `body` al parchear una seccion) caia en silencio al default del campo real y
VACIABA la seccion - detectado en la prueba de humo del 2026-08-12. Un campo
desconocido debe ser 422, nunca ignorarse.
"""

from __future__ import annotations

from tests.test_documents import _make_category, _make_document, auth_headers, client  # noqa: F401


class TestUnknownFieldsAreRejected:
    def test_section_patch_with_wrong_field_name_is_422_not_silent_wipe(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        doc = _make_document(
            client, auth_headers, cat,
            content="# Doc\n\n## Uno\n\ncuerpo original\n",
        )
        resp = client.patch(
            f"/documents/{doc['id']}/section",
            json={"heading": "Uno", "operation": "replace", "content": "esto no es `body`"},
            headers=auth_headers,
        )
        assert resp.status_code == 422, resp.text

        # y el documento sigue intacto
        after = client.get(f"/documents/{cat}/{doc['slug']}", headers=auth_headers)
        assert "cuerpo original" in after.json()["content"]

    def test_document_create_with_unknown_field_is_422(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        resp = client.post(
            "/documents",
            json={"title": "X", "content": "y", "category": cat, "categoria": "typo"},
            headers=auth_headers,
        )
        assert resp.status_code == 422, resp.text

    def test_category_create_with_unknown_field_is_422(self, client, auth_headers):
        resp = client.post(
            "/categories",
            json={"slug": "cat-strict", "name": "X", "descripcion": "typo"},
            headers=auth_headers,
        )
        assert resp.status_code == 422, resp.text
