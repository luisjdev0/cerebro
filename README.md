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
tests/
```
