# KnowledgeOS - Fase 1-3

Memoria persistente self-hosted para agentes de IA. PostgreSQL + pgvector, retrieval
híbrido (vector + full-text en español, fusionado con RRF), ciclo de vida por
supersedencia, audit log, Context Engine (Fase 2) y grafo ligero de relaciones +
timeline (Fase 3). Ver `plan_v2.md` (secciones 4, 5, 6 y 8) para la arquitectura y el
modelo de datos completos.

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

## Endpoints (Fase 1 + Fase 2 + Fase 3)

| Metodo | Ruta | Descripcion |
|---|---|---|
| `POST` | `/contexts` | crear contexto (`slug`, `name`, `kind`, `description?`) |
| `GET` | `/contexts` | listar contextos |
| `POST` | `/memories` | crear memoria; `context` obligatorio; rechaza credenciales (422) |
| `GET` | `/memories/search` | retrieval hibrido: `q`, `context?`, `scope?` (`auto`\|`all`\|`<slug>`, default `auto`), `type?`, `limit?`, `include_superseded?`, `expand?` (Fase 3, default `false`). Devuelve `{results, scope_decision, related}` -- ver "Context Engine" y "Relaciones y timeline" abajo. |
| `PATCH` | `/memories/{id}` | crea version nueva + supersede la anterior (nunca edita in-place) |
| `DELETE` | `/memories/{id}` | `?hard=false` archiva (default), `?hard=true` borra en duro (cascada a sus `memory_edges`) |
| `POST` | `/memories/{id}/edges` | (Fase 3) crea una arista `{to_memory, relation, note?}`; 422 si `relation` no es del vocabulario o `to_memory == id`, 404 si alguna memoria no existe, 409 si la arista ya existe |
| `DELETE` | `/memories/{id}/edges/{edge_id}` | (Fase 3) borra una arista (debe tocar `id`) |
| `GET` | `/memories/{id}/related` | (Fase 3) vecinos a 1 salto (ambas direcciones) + cadena de supersedencia virtual; filtro `relation?` |
| `GET` | `/timeline` | (Fase 3) memorias `episodic`/`decision` ordenadas por fecha efectiva; filtros `context?`, `from?`, `to?`, `limit?=50` |
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

`GET /stats` expone `disambiguations` (total, cuántas se resolvieron `auto`, `agent`,
`user` o `local_model` -- Fase 4, ver abajo) y `preferences_learned` (términos
aprendidos por contexto) -- es la forma más directa de ver al sistema aprender con el
uso; el MCP server lo expone como `memory_stats()`, y `knowledgeos stats` (CLI) lo
formatea para consola.

## Clasificador local opcional (Fase 4)

**OFF por defecto.** plan_v2.md SS8 (Fase 4) es explícito: no tiene sentido entrenar
ni activar un modelo local de desambiguación mientras no exista un dataset real de
ambigüedades resueltas -- hoy no existe. Lo que esta fase construye no es el modelo,
es el **punto de enchufe**: la interfaz `AmbiguityResolver`
(`src/knowledgeos/context_engine.py`) que `decide_scope` invoca *después* de que el
scoring determinista de la Fase 2 ya decidió que un caso es ambiguo, para intentar
resolverlo localmente en vez de devolverlo al agente que llama.

Dos implementaciones:

- **`NullResolver`** (default, `CONTEXT_ENGINE_RESOLVER` sin definir o `"none"`):
  siempre devuelve `None` -- el flujo de hoy (ambigüedad al agente, Fase 2) queda
  **exactamente igual**, byte por byte, mientras esto no se active a propósito.
- **`OllamaResolver`** (`CONTEXT_ENGINE_RESOLVER=ollama`): llama a la API de Ollama
  (`POST {OLLAMA_URL}/api/generate`, default `http://localhost:11434`, modelo
  `OLLAMA_MODEL`, default `qwen2.5:1.5b`) con un prompt corto que lista los
  candidatos (slug + descripción) y pide un slug de respuesta. Timeout de 2s.
  Cualquier fallo -- Ollama no está corriendo, timeout, respuesta no parseable, o un
  slug que no está entre los candidatos -- cae en silencio a `None` (mismo
  comportamiento que `NullResolver`): **esto nunca debe poder romper una búsqueda**.
  No instala ni configura Ollama por ti; solo trae el cliente con este fallback.

Cuando el resolver sí devuelve un slug válido, la ambigüedad se resuelve como si el
propio Context Engine hubiera dominado desde el principio (`scope_decision.mode ==
"auto"`, resultados ya filtrados a ese contexto), pero queda registrada en
`disambiguation_log` con `resolved_by = 'local_model'` -- distinguible en `GET /stats`
/ `knowledgeos stats` de las resoluciones `auto` (scoring determinista) y `agent`
(agente/MCP eligiendo con el contexto de la conversación).

**Cuándo activarlo en serio** (condición del plan, plan_v2.md SS8): (a) hay **≥ ~500
desambiguaciones registradas** -- usa `knowledgeos export-disambiguations` para ver
cuántas hay y exportar el dataset -- **y** (b) hay una razón medida para hacerlo
(latencia, costo, o una política de privacidad estricta de "ni la query sale del
VPS"). Sin ambas condiciones, esto es infraestructura sin usar a propósito -- earn
your complexity (plan_v2.md SS4.2).

Variables de entorno (`src/knowledgeos/config.py`):

| Variable | Default | Uso |
|---|---|---|
| `CONTEXT_ENGINE_RESOLVER` | `none` | `none` (NullResolver) \| `ollama` (OllamaResolver) |
| `OLLAMA_URL` | `http://localhost:11434` | base URL de la API de Ollama |
| `OLLAMA_MODEL` | `qwen2.5:1.5b` | modelo a pedirle a Ollama |

## Relaciones y timeline (Fase 3)

Grafo ligero **en Postgres** (`memory_edges`, `db/migrations/003_edges.sql`) -- nada de
base de grafos dedicada (plan_v2.md SS8, Fase 3). Dos piezas: aristas explícitas entre
memorias, y una línea de tiempo sobre `occurred_at`.

**Vocabulario de relaciones** (controlado, `CHECK` en la tabla -- no es texto libre):
`relates_to` (asociación genérica), `caused_by` (`from` fue causado por `to` --
decisión → su causa), `part_of` (`from` es parte de `to` -- procedimiento → proyecto),
`contradicts` (`from` contradice a `to`), `follows` (`from` ocurrió después de / como
consecuencia de `to` -- episodio → su consecuencia).

```bash
# Crear una arista: la decision "migrar de proveedor" fue causada por "subio el precio"
curl -s -X POST localhost:8000/memories/$DECISION_ID/edges \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"to_memory":"'$CAUSA_ID'","relation":"caused_by","note":"motivo de la decision"}'

# Vecinos a 1 salto (ambas direcciones), opcionalmente filtrados por relacion
curl -s "localhost:8000/memories/$DECISION_ID/related" -H "Authorization: Bearer $TOKEN"
curl -s "localhost:8000/memories/$DECISION_ID/related?relation=caused_by" -H "Authorization: Bearer $TOKEN"

# Borrar una arista
curl -s -X DELETE localhost:8000/memories/$DECISION_ID/edges/$EDGE_ID -H "Authorization: Bearer $TOKEN"
```

`GET /memories/{id}/related` devuelve, para cada vecino, `relation`, `direction`
(`"outgoing"` si `id` es el origen de la relación, `"incoming"` si es el destino),
`note`, `created_by`, y la memoria vecina completa. Además, **automáticamente**,
incluye la cadena de supersedencia (`memories.superseded_by`, ya existente desde Fase
1) como una relación **virtual** `"supersedes"` (`virtual: true`, `edge_id: null`) --
nunca se escribe a `memory_edges`, se deriva en lectura. `relation=supersedes` como
filtro devuelve solo esa cadena; cualquier otro valor del vocabulario filtra solo
aristas reales.

Borrar una memoria en duro (`DELETE /memories/{id}?hard=true`) borra en cascada
(`ON DELETE CASCADE`) todas sus aristas, en ambas direcciones -- no quedan aristas
huérfanas.

### Expansión de retrieval a 1 salto (`expand=true`)

`GET /memories/search?expand=true` añade, además de `results`, un bloque **separado**
`related` con los vecinos directos de los 3 primeros resultados (deduplicados, máx 5,
solo `status=active`). **`related` nunca se mezcla con `results`** -- no debe alterar
las métricas de retrieval (precision/recall/contaminación de `evals/`, ver más abajo).

Regla de contaminación cruzada: si la búsqueda ya resolvió un contexto único (`scope`
explícito o `auto` con un contexto claro), un vecino de **otro** contexto solo se
incluye si la arista es **explícita** (`virtual: false`) -- se marca `cross_context:
true`. La lógica es que una arista explícita es un puente intencional que el
usuario/agente creó a propósito con `POST /memories/{id}/edges`, no una colisión
accidental de vocabulario entre contextos -- por eso no cuenta como la contaminación
que Fase 2 combate.

```bash
curl -s "localhost:8000/memories/search?q=por+que+migramos+de+proveedor&context=infraestructura&expand=true" \
  -H "Authorization: Bearer $TOKEN"
```

### Timeline

`GET /timeline` junta memorias `episodic` y `decision` (las que tienen sentido en una
línea de tiempo), ordenadas por fecha efectiva (`occurred_at`, o `created_at` si no se
especificó) -- pensado para responder "¿qué pasó en X las últimas semanas?".

```bash
curl -s "localhost:8000/timeline?context=infraestructura&from=2026-07-01T00:00:00Z&to=2026-07-31T00:00:00Z&limit=20" \
  -H "Authorization: Bearer $TOKEN"
```

`add_edge`/`delete_edge` quedan registrados en `audit_log` (acciones `add_edge` /
`delete_edge`), igual que el resto de operaciones de escritura.

## Tests

```bash
pytest
```

`tests/test_rrf.py`, `tests/test_security.py`, `tests/test_context_engine.py` y la
parte unitaria de `tests/test_graph.py` (vocabulario de relaciones) son unitarios (sin
base de datos). `tests/test_supersedence.py` y la parte de integracion de
`tests/test_graph.py` (aristas, no-duplicados, direccion en `related`, cascada de
hard-delete, ordering de `timeline`) se saltan automaticamente si `DATABASE_URL` no es
alcanzable (arranca `docker compose up -d` primero).

## Conectar a Claude (servidor MCP)

`src/knowledgeos/mcp_server.py` expone la API como un servidor MCP por stdio (SDK
oficial `mcp`, `FastMCP`). Es un adaptador delgado: cada tool llama a la API HTTP con
`httpx`, no hay lógica de negocio propia (salvo el pequeño estado de proceso descrito
abajo para el aprendizaje de desambiguaciones). Tools disponibles: `memory_search`,
`memory_remember`, `memory_update`, `memory_forget`, `memory_contexts`,
`memory_create_context`, `memory_stats`, `memory_link`, `memory_related`,
`memory_timeline` (las últimas tres, Fase 3).

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
`memory_search` también acepta `expand=True` (Fase 3, default `False`) para recibir un
bloque `related` con los vecinos directos de los resultados -- ver "Relaciones y
timeline (Fase 3)" arriba.

`memory_link(from_memory_id, to_memory_id, relation, note?)` crea una arista explícita
entre dos memorias (vocabulario: `relates_to`, `caused_by`, `part_of`, `contradicts`,
`follows`); su docstring explica cuándo usar cada una (decisiones→causas,
procedimientos→proyectos, episodios→consecuencias). `memory_related(memory_id,
relation?)` lista los vecinos a 1 salto, incluida la cadena de supersedencia virtual.
`memory_timeline(context?, from_date?, to_date?, limit?)` responde preguntas tipo
"¿qué pasó en X las últimas semanas?".

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

## CLI

`src/knowledgeos/cli.py` (entry point de consola `knowledgeos`, instalado por
`pip install -e ".[dev]"`) es un cliente delgado de la API HTTP -- igual que el
servidor MCP, no tiene lógica de negocio propia (salvo la orquestación del importador
de Markdown, ver abajo). Lee `.env` (vía `knowledgeos.config.get_settings`) para el
token; por defecto asume la API en `http://localhost:<APP_PORT>` y puede
sobreescribirse con las mismas variables que el servidor MCP:
`KNOWLEDGEOS_API_URL` / `KNOWLEDGEOS_API_TOKEN`. `backup`/`restore` son la excepción:
hablan directo con `docker compose` (Postgres solo expone su puerto en `localhost`, y
un dump no tiene sentido como llamada HTTP).

```bash
knowledgeos --help

# dataset de disambiguation_log para un futuro fine-tuning local (Fase 4)
knowledgeos export-disambiguations --output disambiguations.jsonl
knowledgeos export-disambiguations --resolved-only

# estadisticas del sistema (igual que GET /stats), formateadas para consola
knowledgeos stats

# backup / restore (pg_dump / psql via docker compose)
knowledgeos backup --output backups/
knowledgeos restore backups/knowledgeos-20260807-010359.sql   # pide confirmacion
knowledgeos restore backups/knowledgeos-20260807-010359.sql --yes   # sin confirmar
```

`export-disambiguations` siempre imprime cuántos ejemplos hay frente al umbral del
plan (`~500`, ver "Clasificador local opcional (Fase 4)" arriba) para que sea fácil
saber si ya vale la pena considerar el fine-tuning.

### Importar memorias existentes (Fase 5)

`knowledgeos import-markdown` es el **primer conector de Fase 5** (plan_v2.md SS8):
importa archivos Markdown de memoria ya existentes (`MEMORY.md`/`CLAUDE.md` estilo
Claude Code, o notas sueltas) como memorias de KnowledgeOS. Se eligió como conector #1
a propósito porque resuelve la migración desde el statu quo del usuario, no porque sea
técnicamente lo más interesante (plan_v2.md SS11: "sin dogfooding no hay dataset").

El parsing (`src/knowledgeos/markdown_importer.py`) reconoce tres formatos, en este
orden:

1. **Frontmatter YAML estilo memoria de Claude Code** (`name`, `description`,
   `metadata.type`) -> una memoria por archivo. `description` se usa como título,
   el cuerpo (sin el frontmatter) como contenido. Mapeo de `metadata.type`:
   `user`/`feedback`/`reference` -> `semantic`; `project` -> `semantic` con
   `importance=0.7`.
2. **Índice `MEMORY.md`** (líneas `- [título](archivo.md) — hook`): si el archivo
   enlazado existe, se sigue el link y se parsea recursivamente (con el mismo
   dispatch: puede a su vez tener frontmatter); si no existe, el bullet mismo se
   vuelve una memoria pequeña (`title`, `content=hook`).
3. **Markdown genérico** (fallback): se divide por headings de nivel 1-2; cada
   sección con >= 2 líneas de contenido real es una memoria (`title`=heading,
   `content`=cuerpo); las secciones más chicas se fusionan con la anterior.

En cualquiera de los tres casos, bloques de código de más de 30 líneas se truncan a
`[código truncado]` antes de procesar -- una memoria es un resumen destilado, no un
volcado de código fuente (plan_v2.md SS4.1, "memory over conversation").

```bash
# vista previa: que se importaria, sin escribir nada
knowledgeos import-markdown ./mis-notas --context notas-personales --dry-run

# import real; crea el contexto si no existe
knowledgeos import-markdown ./mis-notas \
  --context notas-personales --create-context \
  --context-description "Notas migradas desde Markdown"

# un solo archivo, tipo forzado
knowledgeos import-markdown ./MEMORY.md --context notas-personales --type semantic
```

Antes de insertar cada memoria, el importador busca por similitud (`GET
/memories/search` acotado al contexto destino) usando el propio contenido como query;
si el resultado top tiene un score de RRF alto **y** el mismo título exacto, la salta
y la reporta como "duplicada" en vez de reinsertarla -- así una segunda corrida sobre
el mismo directorio (o un `MEMORY.md` que enlaza archivos que el glob recursivo ya
recorrió por separado) no duplica memorias. Credenciales detectadas por la API
(`POST /memories` -> 422, ver `src/knowledgeos/security.py`) se capturan y reportan
como "rechazada" sin interrumpir el resto del import. Al final imprime un resumen:
`N importadas, M duplicadas (saltadas), K rechazadas`.

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

**Fase 3 (grafo/timeline) re-verificada sin regresión:** el harness no pasa `expand`
en sus búsquedas y `memory_edges`/`/timeline` son endpoints nuevos que el corpus de
`evals/` no toca, así que la tabla de arriba se re-corrió tal cual tras Fase 3 y dio
exactamente los mismos números (`scope=auto`: 0% contaminación en los 3 categorías,
100% recall; `scope=all` control: 25% contaminación en ambiguo, igual que antes).

**Fase 4/5 (resolver opcional + importador de Markdown) re-verificada sin regresión:**
`CONTEXT_ENGINE_RESOLVER` no está seteado en el entorno de evaluación (`NullResolver`,
default), así que el hook de la Fase 4 es un no-op garantizado; `import-markdown` es
un comando de CLI que el harness nunca invoca. Se re-corrió la tabla de arriba
(`scope=auto`) tras ambas tareas y dio los mismos números: 0% contaminación en las 3
categorías, 100% recall.

## Estructura

```
compose.yaml                  # postgres con pgvector; puerto 5432 solo en localhost
.env.example                  # DATABASE_URL, API_TOKEN, EMBEDDING_MODEL, EMBEDDING_DIMENSION
db/migrations/001_init.sql    # schema Fase 1 (contexts, memories, audit_log)
db/migrations/002_context_engine.sql   # schema Fase 2 (disambiguation_log, context_preferences)
db/migrations/003_edges.sql   # schema Fase 3 (memory_edges: grafo ligero de relaciones)
src/knowledgeos/
    config.py                 # settings desde env (pydantic-settings), incluye CONTEXT_ENGINE_*
    db.py                     # pool asyncpg + aplicacion de migraciones al arrancar
    embeddings.py             # EmbeddingProvider (fastembed local, con fallback a sentence-transformers)
    security.py                # deteccion de credenciales en remember()
    retrieval.py               # busqueda hibrida (vector + full-text) fusionada con RRF
    context_engine.py          # Context Engine (Fase 2) + AmbiguityResolver/NullResolver/OllamaResolver (Fase 4)
    graph.py                   # Fase 3: aristas (memory_edges), related() 1-hop, timeline, expand de search
    api.py                     # FastAPI app (auth, CRUD, search con scoping, disambiguations, stats, edges, timeline)
    markdown_importer.py       # Fase 5: parsing puro (frontmatter / indice MEMORY.md / generico por headings)
    cli.py                     # entry point `knowledgeos`: export-disambiguations, stats, import-markdown, backup/restore
    main.py                    # uvicorn entrypoint
    mcp_server.py              # servidor MCP (FastMCP, stdio) - adaptador delgado sobre la API
evals/
    harness/adapters/knowledgeos_adapter.py   # adaptador del harness contra la API real
tests/
    test_context_engine.py    # unitarios: dominancia, empate->ambiguo, boost por preferencias
    test_ambiguity_resolver.py # Fase 4: NullResolver, OllamaResolver mockeado (valida/invalida/timeout)
    test_markdown_importer.py # Fase 5: frontmatter, indice MEMORY.md, genérico por headings (fixtures en tests/fixtures/)
    test_graph.py              # Fase 3: vocabulario (unitario) + aristas/related/timeline (integracion)
```
