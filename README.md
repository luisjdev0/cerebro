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

## Endpoints (Fase 1 + Fase 2)

| Metodo | Ruta | Descripcion |
|---|---|---|
| `POST` | `/contexts` | crear contexto (`slug`, `name`, `kind`, `description?`) |
| `GET` | `/contexts` | listar contextos |
| `POST` | `/memories` | crear memoria; `context` obligatorio; rechaza credenciales (422) |
| `GET` | `/memories/search` | retrieval hibrido: `q`, `context?`, `scope?` (`auto`\|`all`\|`<slug>`, default `auto`), `type?`, `limit?`, `include_superseded?`. Devuelve `{results, scope_decision}` -- ver "Context Engine" abajo. |
| `PATCH` | `/memories/{id}` | crea version nueva + supersede la anterior (nunca edita in-place) |
| `DELETE` | `/memories/{id}` | `?hard=false` archiva (default), `?hard=true` borra en duro |
| `POST` | `/disambiguations/{id}/resolve` | resuelve una desambiguacion pendiente (`{"context": "<slug>"}`); hace crecer `context_preferences` |
| `GET` | `/stats` | memorias por contexto/estado, desambiguaciones (total/auto/agent/user), preferencias aprendidas |
| `GET` | `/health` | sin auth; chequea conexion a la base de datos |

Header opcional `X-Agent-Name` identifica al agente que llama (usado en `source` y en
`audit_log`; default `"unknown"`).

## Context Engine (Fase 2)

`GET /memories/search` decide el *scope* de la búsqueda antes de aplicar el retrieval
final (plan_v2.md SS7). Tres modos, vía el parámetro `scope`:

- **`scope=auto`** (default): corre el Context Engine. Es determinista y barato -- **sin
  llamadas a LLM** -- y decide en dos pasos:
  1. Retrieval híbrido preliminar sin filtro (top ~20) y suma el score RRF de cada
     contexto, más un boost por `context_preferences` (términos ya asociados a un
     contexto por resoluciones anteriores) y un boost si la query nombra el contexto
     explícitamente.
  2. Si el contexto mejor puntuado **domina** (su share normalizado supera
     `CONTEXT_ENGINE_DOMINANCE_THRESHOLD` *y* su margen sobre el segundo supera
     `CONTEXT_ENGINE_MARGIN_THRESHOLD`) -> `scope_decision.mode = "auto"`, la búsqueda
     ya viene filtrada a ese contexto.
  3. Si no domina -> `scope_decision.mode = "ambiguous"`: `results` viene **vacío a
     propósito** (nunca se devuelven memorias de contextos distintos mezcladas a
     ciegas). En su lugar, `scope_decision.candidates` trae 2-4 contextos posibles
     (slug, nombre, descripción, score) y `scope_decision.results_by_candidate` trae
     2-3 resultados reales de cada uno, como evidencia para que quien llama decida.
     `scope_decision.disambiguation_id` identifica la desambiguación pendiente.
- **`scope=all`**: sin Context Engine, retrieval híbrido puro sobre todos los
  contextos -- el comportamiento de Fase 1, usado como control del benchmark.
- **`scope=<slug>`** (o pasar `context=<slug>` directamente): filtra explícitamente,
  sin invocar el engine -- `scope_decision.mode = "explicit"`.

**Aprendizaje:** `POST /disambiguations/{id}/resolve {"context": "<slug>"}` registra
la elección (`resolved_by='agent'`, o `'auto'` cuando el propio engine ya dominaba) y
hace crecer `context_preferences`: los tokens significativos de la query (normalizados,
sin stopwords ES) suman peso hacia el contexto elegido. Preguntas parecidas en el
futuro se inclinan hacia ese contexto -- y, con suficiente refuerzo, terminan
resolviéndose solas en modo `auto` en vez de volver a ser ambiguas. El boost por
preferencia es deliberadamente pequeño por unidad de peso (`CONTEXT_ENGINE_PREFERENCE_BOOST_PER_WEIGHT`,
default `0.008`): un solo término genérico que colisiona legítimamente entre contextos
(p.ej. "mes", "costos") no debe poder tumbar la señal real de retrieval por una sola
resolución; hace falta refuerzo consistente.

Umbrales configurables por entorno (nombres `CONTEXT_ENGINE_*`, ver
`src/knowledgeos/config.py` para la lista completa y los defaults calibrados contra
`evals/`).

`GET /stats` expone `disambiguations` (total, cuántas se resolvieron `auto` vs
`agent`) y `preferences_learned` (términos aprendidos por contexto) -- es la forma más
directa de ver al sistema aprender con el uso; el MCP server lo expone como
`memory_stats()`.

## Tests

```bash
pytest
```

`tests/test_rrf.py`, `tests/test_security.py` y `tests/test_context_engine.py` son
unitarios (sin base de datos). `tests/test_supersedence.py` es de integracion: se
salta automaticamente si `DATABASE_URL` no es alcanzable (arranca `docker compose up
-d` primero).

## Conectar a Claude (servidor MCP)

`src/knowledgeos/mcp_server.py` expone la API como un servidor MCP por stdio (SDK
oficial `mcp`, `FastMCP`). Es un adaptador delgado: cada tool llama a la API HTTP con
`httpx`, no hay lógica de negocio propia (salvo el pequeño estado de proceso descrito
abajo para el aprendizaje de desambiguaciones). Tools disponibles: `memory_search`,
`memory_remember`, `memory_update`, `memory_forget`, `memory_contexts`,
`memory_create_context`, `memory_stats`.

`memory_search` usa `scope=auto` por defecto (Context Engine). Si la respuesta es
ambigua, `message` trae el texto ya formateado para decidir o mostrar al usuario, y
`candidates`/`results_by_candidate` la evidencia cruda. El servidor recuerda en
memoria de proceso (una sola casilla, no historial) el `disambiguation_id` de la
última búsqueda ambigua; si la SIGUIENTE llamada a `memory_search` pasa `context`
explícito, asume que así se resolvió esa ambigüedad y llama automáticamente a
`POST /disambiguations/{id}/resolve` -- sin que el agente tenga que hacerlo a mano.
Eso alimenta `context_preferences`, así que preguntas parecidas tienden a resolverse
solas la próxima vez. `memory_stats()` expone conteos de memorias, desambiguaciones
(auto vs agent) y preferencias aprendidas -- útil para ver el aprendizaje en acción.

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

# control (Fase 1): retrieval hibrido sin Context Engine
KNOWLEDGEOS_SEARCH_SCOPE=all python evals/harness/run_eval.py --adapter knowledgeos --include-superseded

# Context Engine (Fase 2): scope=auto
KNOWLEDGEOS_SEARCH_SCOPE=auto python evals/harness/run_eval.py --adapter knowledgeos --include-superseded
```

`evals/harness/adapters/knowledgeos_adapter.py` habla con la API real por HTTP (igual
que lo haría el servidor MCP): en `setup()` verifica `/health`, crea los contextos del
corpus que falten y purga memorias de corridas anteriores. `KNOWLEDGEOS_SEARCH_SCOPE`
(default `all`) elige el modo: `all` es el retrieval híbrido puro de Fase 1 (sin
scoping); `auto` activa el Context Engine -- si la API responde `ambiguous`, el
adaptador simula un agente razonable (sin leer `contexto_esperado`: elige el
candidato cuyo resultado individual top tenga mejor score, usando el score agregado
del propio candidato como desempate) y llama `resolve` como haría el servidor MCP.

Los 3 pares `superseded`→`active` del corpus (`evals/memories.yaml`,
`superseded_by_id`) se insertan como cadena de supersedencia real cuando se usa
`--include-superseded`: `POST` la versión vieja, `PATCH` con el contenido de la
nueva -- el mismo camino que produciría `memory_update()` en producción, en vez de
insertar ambas como filas activas independientes.

**Última calibración medida** (k=5, `--include-superseded`, corpus de `evals/`):

| Modo | Categoría | Precision@5 | Recall@5 | Contaminación |
|---|---|---|---|---|
| `scope=all` (control) | ambiguo | 20% | 100% | 25% |
| `scope=all` (control) | directo | 20% | 100% | 0% |
| `scope=all` (control) | temporal | 20% | 100% | 0% |
| `scope=auto` (Context Engine) | ambiguo | 20% | 100% | **0%** |
| `scope=auto` (Context Engine) | directo | 20% | 100% | 0% |
| `scope=auto` (Context Engine) | temporal | 20% | 100% | 0% |

Umbrales calibrados en `src/knowledgeos/config.py` (`CONTEXT_ENGINE_*`); entre corridas
del benchmark, trunca `disambiguation_log` y `context_preferences` para medir
`scope=auto` en frío (sin aprendizaje acumulado de una corrida anterior).

Lee `evals/README.md` para cómo agregar casos o corpus propios, y `--include-superseded`
para que la categoría `temporal` sea significativa.

## Estructura

```
compose.yaml                  # postgres con pgvector; puerto 5432 solo en localhost
.env.example                  # DATABASE_URL, API_TOKEN, EMBEDDING_MODEL, EMBEDDING_DIMENSION
db/migrations/001_init.sql    # schema Fase 1 (contexts, memories, audit_log)
db/migrations/002_context_engine.sql   # schema Fase 2 (disambiguation_log, context_preferences)
src/knowledgeos/
    config.py                 # settings desde env (pydantic-settings), incluye CONTEXT_ENGINE_*
    db.py                     # pool asyncpg + aplicacion de migraciones al arrancar
    embeddings.py             # EmbeddingProvider (fastembed local, con fallback a sentence-transformers)
    security.py                # deteccion de credenciales en remember()
    retrieval.py               # busqueda hibrida (vector + full-text) fusionada con RRF
    context_engine.py          # Context Engine (Fase 2): scoring, umbrales, aprendizaje de preferencias
    api.py                     # FastAPI app (auth, CRUD, search con scoping, disambiguations, stats)
    main.py                    # uvicorn entrypoint
    mcp_server.py              # servidor MCP (FastMCP, stdio) - adaptador delgado sobre la API
evals/
    harness/adapters/knowledgeos_adapter.py   # adaptador del harness contra la API real
tests/
    test_context_engine.py    # unitarios: dominancia, empate->ambiguo, boost por preferencias
```
