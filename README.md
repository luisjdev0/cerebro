# KnowledgeOS - Fase 1

Memoria persistente self-hosted para agentes de IA. PostgreSQL + pgvector, retrieval
híbrido (vector + full-text en español, fusionado con RRF), ciclo de vida por
supersedencia, audit log. Ver `plan_v2.md` (secciones 4, 5 y 6) para la arquitectura y
el modelo de datos completos.

## Levantar todo en 5 minutos

Requisitos: Docker Desktop corriendo, Python 3.11+.

```bash
# 1. Base de datos
docker compose up -d
# espera a que este "healthy":
docker compose ps

# 2. Entorno Python
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
# source .venv/bin/activate
pip install -e ".[dev]"

# 3. Configuracion
cp .env.example .env
# los valores por defecto ya funcionan contra el compose.yaml de este repo;
# cambia API_TOKEN antes de exponer el servicio fuera de tu maquina.

# 4. Arrancar la API (aplica migraciones automaticamente al iniciar)
python -m knowledgeos.main
# o: uvicorn knowledgeos.main:app --reload
```

La API queda en `http://localhost:8000`. `GET /health` no requiere auth; el resto de
endpoints requieren `Authorization: Bearer <API_TOKEN>` (ver `.env`).

La primera vez que arranca, descarga el modelo de embeddings
(`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` vía `fastembed`, ~9s,
luego queda cacheado localmente por `fastembed`/`huggingface_hub`).

## Uso rapido

```bash
TOKEN=change-me-dev-token

curl -s -X POST localhost:8000/contexts \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"slug":"finanzas-personales","name":"Finanzas personales","kind":"domain","description":"Gastos e ingresos personales"}'

curl -s -X POST localhost:8000/memories \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"content":"Este mes gaste 450 dolares en supermercado","context":"finanzas-personales","type":"episodic"}'

curl -s "localhost:8000/memories/search?q=cuanto+gaste+este+mes&context=finanzas-personales" \
  -H "Authorization: Bearer $TOKEN"
```

## Endpoints (Fase 1)

| Metodo | Ruta | Descripcion |
|---|---|---|
| `POST` | `/contexts` | crear contexto (`slug`, `name`, `kind`, `description?`) |
| `GET` | `/contexts` | listar contextos |
| `POST` | `/memories` | crear memoria; `context` obligatorio; rechaza credenciales (422) |
| `GET` | `/memories/search` | retrieval hibrido: `q`, `context?`, `type?`, `limit?`, `include_superseded?` |
| `PATCH` | `/memories/{id}` | crea version nueva + supersede la anterior (nunca edita in-place) |
| `DELETE` | `/memories/{id}` | `?hard=false` archiva (default), `?hard=true` borra en duro |
| `GET` | `/health` | sin auth; chequea conexion a la base de datos |

Header opcional `X-Agent-Name` identifica al agente que llama (usado en `source` y en
`audit_log`; default `"unknown"`).

## Tests

```bash
pytest
```

`tests/test_rrf.py` y `tests/test_security.py` son unitarios (sin base de datos).
`tests/test_supersedence.py` es de integracion: se salta automaticamente si
`DATABASE_URL` no es alcanzable (arranca `docker compose up -d` primero).

## Conectar a Claude (servidor MCP)

`src/knowledgeos/mcp_server.py` expone la API como un servidor MCP por stdio (SDK
oficial `mcp`, `FastMCP`). Es un adaptador delgado: cada tool llama a la API HTTP con
`httpx`, no hay lógica de negocio propia. Tools disponibles: `memory_search`,
`memory_remember`, `memory_update`, `memory_forget`, `memory_contexts`,
`memory_create_context`.

Tras `pip install -e ".[dev]"` queda instalado el entry point de consola
`knowledgeos-mcp` (ver `[project.scripts]` en `pyproject.toml`). Requiere que la API
esté corriendo (`python -m knowledgeos.main`).

Variables de entorno que lee el servidor MCP:

| Variable | Default | Uso |
|---|---|---|
| `KNOWLEDGEOS_API_URL` | `http://localhost:8000` | base URL de la API HTTP |
| `KNOWLEDGEOS_API_TOKEN` | *(vacío)* | debe coincidir con `API_TOKEN` del `.env` de la API |
| `KNOWLEDGEOS_AGENT_NAME` | `mcp-client` | identidad enviada como `X-Agent-Name` (audit log, `memory.source`) |

### Claude Code

```bash
claude mcp add knowledgeos --scope user \
  -e KNOWLEDGEOS_API_URL=http://localhost:8000 \
  -e KNOWLEDGEOS_API_TOKEN=change-me-dev-token \
  -e KNOWLEDGEOS_AGENT_NAME=claude-code \
  -- knowledgeos-mcp
```

### Claude Desktop

Agrega esto a `claude_desktop_config.json` (menú Claude > Settings > Developer > Edit
Config):

```json
{
  "mcpServers": {
    "knowledgeos": {
      "command": "D:\\dev\\jobs\\luisjdev\\cerebro\\.venv\\Scripts\\knowledgeos-mcp.exe",
      "env": {
        "KNOWLEDGEOS_API_URL": "http://localhost:8000",
        "KNOWLEDGEOS_API_TOKEN": "change-me-dev-token",
        "KNOWLEDGEOS_AGENT_NAME": "claude-desktop"
      }
    }
  }
}
```

Si `knowledgeos-mcp` no está en el `PATH` que ve Claude Desktop, usa la ruta absoluta
al ejecutable del venv, p.ej. en Windows:
`"command": "D:\\ruta\\al\\repo\\.venv\\Scripts\\knowledgeos-mcp.exe"`.

## Evaluación

La suite de evaluación de retrieval (`evals/`, ver `evals/README.md` para el detalle
completo de métricas y corpus) mide precision@k, recall@k y tasa de contaminación
entre contextos, con un corpus sintético de ~40 memorias en 6 contextos y 30 casos de
prueba en español.

```bash
# baseline: overlap de palabras clave, sin nocion de contexto
python evals/harness/run_eval.py --adapter naive

# KnowledgeOS real, vía la API HTTP (requiere la API corriendo y Postgres arriba)
python -m knowledgeos.main &   # o en otra terminal
python evals/harness/run_eval.py --adapter knowledgeos
```

`evals/harness/adapters/knowledgeos_adapter.py` habla con la API real por HTTP (igual
que lo haría el servidor MCP): en `setup()` verifica `/health`, crea los contextos del
corpus que falten y purga memorias de corridas anteriores; `search()` busca **sin**
pasar `context` a propósito -- la Fase 1 no hace scoping automático (eso es la Fase 2,
plan_v2.md §7), así que el benchmark mide el retrieval híbrido tal como lo vería hoy
un agente que aún no sabe a qué contexto pertenece la pregunta.

Lee `evals/README.md` para cómo agregar casos o corpus propios, y `--include-superseded`
para que la categoría `temporal` sea significativa.

## Estructura

```
compose.yaml                  # postgres con pgvector; puerto 5432 solo en localhost
.env.example                  # DATABASE_URL, API_TOKEN, EMBEDDING_MODEL, EMBEDDING_DIMENSION
db/migrations/001_init.sql    # schema (contexts, memories, audit_log)
src/knowledgeos/
    config.py                 # settings desde env (pydantic-settings)
    db.py                     # pool asyncpg + aplicacion de migraciones al arrancar
    embeddings.py             # EmbeddingProvider (fastembed local, con fallback a sentence-transformers)
    security.py                # deteccion de credenciales en remember()
    retrieval.py               # busqueda hibrida (vector + full-text) fusionada con RRF
    api.py                     # FastAPI app (auth, CRUD, audit log)
    main.py                    # uvicorn entrypoint
    mcp_server.py              # servidor MCP (FastMCP, stdio) - adaptador delgado sobre la API
evals/
    harness/adapters/knowledgeos_adapter.py   # adaptador del harness contra la API real
tests/
```
