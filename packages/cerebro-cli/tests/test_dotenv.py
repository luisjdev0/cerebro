"""Parser dotenv minimo y precedencia de `load_repo_dotenv` (variable ya presente en
el entorno nunca se pisa; `.env.production` gana sobre `.env`).

Ningun test aqui llama al VPS real ni imprime un token -- todo corre contra
archivos .env de prueba en tmp_path y diccionarios de entorno falsos, nunca contra
`os.environ` real ni el `.env.production` real del repo.
"""

from __future__ import annotations

from cerebro_cli.dotenv import load_repo_dotenv, parse_dotenv


class TestParseDotenv:
    def test_simple_key_value(self):
        assert parse_dotenv("FOO=bar\n") == {"FOO": "bar"}

    def test_ignores_blank_lines_and_comments(self):
        text = "\n# comentario\nFOO=bar\n\n# otro comentario\nBAZ=qux\n"
        assert parse_dotenv(text) == {"FOO": "bar", "BAZ": "qux"}

    def test_ignores_lines_without_equals(self):
        text = "esto no es una variable\nFOO=bar\n"
        assert parse_dotenv(text) == {"FOO": "bar"}

    def test_strips_surrounding_quotes(self):
        text = 'FOO="bar baz"\nQUX=\'algo\'\n'
        assert parse_dotenv(text) == {"FOO": "bar baz", "QUX": "algo"}

    def test_value_may_contain_equals_signs(self):
        text = "URL=https://example.com/x?a=1&b=2\n"
        assert parse_dotenv(text) == {"URL": "https://example.com/x?a=1&b=2"}

    def test_empty_text_yields_empty_dict(self):
        assert parse_dotenv("") == {}


class TestLoadRepoDotenvPrecedence:
    def test_missing_files_is_a_noop(self, tmp_path):
        env: dict[str, str] = {}
        load_repo_dotenv(tmp_path, env=env)
        assert env == {}

    def test_loads_values_from_env_file(self, tmp_path):
        (tmp_path / ".env").write_text("FOO=bar\n", encoding="utf-8")
        env: dict[str, str] = {}
        load_repo_dotenv(tmp_path, env=env)
        assert env == {"FOO": "bar"}

    def test_does_not_overwrite_variable_already_in_process_env(self, tmp_path):
        (tmp_path / ".env").write_text("FOO=from-dotenv\n", encoding="utf-8")
        env = {"FOO": "from-shell-export"}
        load_repo_dotenv(tmp_path, env=env)
        assert env["FOO"] == "from-shell-export"  # nunca se pisa

    def test_env_production_wins_over_env_when_both_define_same_key(self, tmp_path):
        (tmp_path / ".env.production").write_text("FOO=from-production\n", encoding="utf-8")
        (tmp_path / ".env").write_text("FOO=from-dev\n", encoding="utf-8")
        env: dict[str, str] = {}
        load_repo_dotenv(tmp_path, env=env)
        assert env["FOO"] == "from-production"

    def test_keys_unique_to_env_are_still_loaded_when_env_production_exists(self, tmp_path):
        (tmp_path / ".env.production").write_text("A=1\n", encoding="utf-8")
        (tmp_path / ".env").write_text("A=2\nB=3\n", encoding="utf-8")
        env: dict[str, str] = {}
        load_repo_dotenv(tmp_path, env=env)
        assert env == {"A": "1", "B": "3"}

    def test_process_env_wins_over_both_files(self, tmp_path):
        (tmp_path / ".env.production").write_text("FOO=from-production\n", encoding="utf-8")
        (tmp_path / ".env").write_text("FOO=from-dev\n", encoding="utf-8")
        env = {"FOO": "from-shell-export"}
        load_repo_dotenv(tmp_path, env=env)
        assert env["FOO"] == "from-shell-export"


class TestResolvesVpsUrlWithoutContactingIt:
    """Demuestra que, con solo un .env.production presente y sin variables de entorno
    manuales, `cerebro_clients.config.memory_base_url()` resuelve a la URL del VPS --
    sin hacer NINGUNA llamada de red. Usa una URL/token FALSOS en un .env.production
    de prueba en tmp_path, nunca el archivo real del repo ni una llamada HTTP real."""

    def test_memory_base_url_resolves_to_fake_vps_url_after_loading_env_production(self, tmp_path, monkeypatch):
        from cerebro_clients import config as clients_config

        monkeypatch.delenv("CEREBRO_MEMORY_URL", raising=False)
        monkeypatch.delenv("KNOWLEDGEOS_API_URL", raising=False)
        monkeypatch.delenv("CEREBRO_TOKEN", raising=False)
        monkeypatch.delenv("KNOWLEDGEOS_API_TOKEN", raising=False)

        (tmp_path / ".env.production").write_text(
            "KNOWLEDGEOS_API_URL=https://vps-de-prueba.invalid\n"
            "KNOWLEDGEOS_API_TOKEN=token-de-prueba-no-real\n",
            encoding="utf-8",
        )

        # Simula lo que hace main(): carga sin pisar nada del entorno real. Se aplica
        # a un dict aparte y luego se vuelca a os.environ via monkeypatch.setenv (en
        # vez de mutar os.environ directamente) para que pytest deshaga el cambio solo
        # al terminar el test, sin filtrar la URL/token falsos a otros tests.
        loaded: dict[str, str] = {}
        load_repo_dotenv(tmp_path, env=loaded)
        for key, value in loaded.items():
            monkeypatch.setenv(key, value)

        assert clients_config.memory_base_url() == "https://vps-de-prueba.invalid"
        assert clients_config.memory_token() == "token-de-prueba-no-real"
