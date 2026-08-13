# cerebro

Ecosistema self-hosted y agnóstico al modelo de memoria persistente y documentación
para agentes de IA (Claude, GPT, Gemini, agentes propios). Nació como un único paquete
(`knowledgeos`) y hoy es un monorepo de cinco paquetes bajo `packages/`:

| Paquete | Qué es | Entry point |
|---|---|---|
| [`cerebro-memory`](packages/cerebro-memory) | Servicio API de memoria persistente: PostgreSQL + pgvector, retrieval híbrido (vector + full-text en español, fusionado con RRF), ciclo de vida por supersedencia, audit log, Context Engine (desambiguación de contexto sin LLM), grafo ligero de relaciones + timeline, tokens con scopes | *(ninguno — servicio API puro, sin CLI ni MCP propios)* |
| [`cerebro-docs`](packages/cerebro-docs) | Servicio API hermano: repositorio de documentos Markdown completos, categorizables, versionados, con parches parciales por sección y búsqueda full-text | *(ninguno — servicio API puro)* |
| [`cerebro-clients`](packages/cerebro-clients) | SDK delgado `httpx` compartido (`MemoryClient`, `DocsClient`) que habla con ambas APIs | *(librería, no ejecutable)* |
| [`cerebro-mcp`](packages/cerebro-mcp) | Servidor MCP único por stdio que expone ambos servicios como tools (`memory_*` + `docs_*`) | `cerebro-mcp` |
| [`cerebro-cli`](packages/cerebro-cli) | CLI único (`cerebro memory ...`, `cerebro docs ...`, más comandos transversales) | `cerebro` |

`cerebro-memory` y `cerebro-docs` no tienen lógica de negocio duplicada entre sí: cada
uno es dueño de su propio schema en el mismo Postgres (`cerebro_memory` /
`cerebro_docs`) y su propia auth. Todo cliente (`cerebro-mcp`, `cerebro-cli`, o
cualquier integración futura) pasa por `cerebro-clients` y por el mismo camino HTTP de
auth + scopes + audit log de cada API — no hay atajos ni lógica de negocio duplicada
en la capa de cliente.

v1.0 de `cerebro-memory` = Fases 1-3 sólidas + evaluación pasando + dogfooding
sostenido (ver `plan_v2.md` SS8, en la raíz del repo, para la arquitectura y el
modelo de datos completos de ese servicio); el clasificador local, el grafo y los
conectores externos son mejoras encima de esa base, no requisitos. `cerebro-docs` es
más reciente y más simple: sin retrieval semántico, sin Context Engine, solo full-text
simple sobre contenido versionado.

## Quickstart

Tres caminos según lo que quieras hacer - elige uno:

### A. Desarrollo local (recomendado para dogfooding / seguir desarrollando)

Ambas APIs corriendo directo con Python, solo Postgres en Docker. Es el modo más
rápido para iterar (recarga instantánea, logs en tu propia terminal).

Requisitos: Docker Desktop corriendo, Python 3.11+.

```bash
# 1. Base de datos (docker compose sin --profile solo levanta postgres)
docker compose up -d
docker compose ps   # espera a que este "healthy"

# 2. Entorno Python
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac

# instala los 5 paquetes en un solo comando (necesario: cerebro-cli y cerebro-mcp
# dependen de cerebro-clients/cerebro-memory por nombre, no estan en PyPI, asi que
# pip los resuelve entre si solo si se le pasan todos juntos)
pip install -e "packages/cerebro-clients[dev]" -e "packages/cerebro-memory[dev]" ^
            -e "packages/cerebro-docs[dev]" -e "packages/cerebro-mcp[dev]" ^
            -e "packages/cerebro-cli[dev]"
# Linux/Mac: mismo comando pero con \ en vez de ^ como continuador de linea

# 3. Configuracion (variables compartidas: DATABASE_URL, API_TOKEN, APP_PORT, etc.)
cp .env.example .env
# los valores por defecto ya funcionan contra el compose.yaml de este repo;
# cambia API_TOKEN antes de exponer cualquier servicio fuera de tu maquina (ver "Seguridad").

# 4. Arrancar cerebro-memory (aplica migraciones automaticamente al iniciar, puerto 8000)
python -m cerebro_memory.main
# o: uvicorn cerebro_memory.main:app --reload

# 5. Arrancar cerebro-docs en OTRA terminal, con su propio puerto/token (comparte
# DATABASE_URL/POSTGRES_PASSWORD via el mismo .env, pero necesita su propio APP_PORT/
# API_TOKEN para no pisar los de cerebro-memory - ver seccion "cerebro-docs" del
# .env.example):
$env:APP_PORT=8010; $env:API_TOKEN="change-me-dev-token-docs"; python -m cerebro_docs.main   # PowerShell
# APP_PORT=8010 API_TOKEN=change-me-dev-token-docs python -m cerebro_docs.main               # bash
```

La primera vez que arranca `cerebro-memory`, descarga el modelo de embeddings
(`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` vía `fastembed`, ~9s,
luego queda cacheado localmente por `fastembed`/`huggingface_hub`). `cerebro-docs` no
usa embeddings (full-text simple), arranca al instante.

### B. Todo en Docker (producción / probar el deploy real)

Postgres **y** ambas APIs en contenedores, sin instalar Python en el host. Cada API se
sirve desde su propia imagen multi-stage (`packages/cerebro-memory/Dockerfile`,
`packages/cerebro-docs/Dockerfile`); la de `cerebro-memory` pre-descarga el modelo de
embeddings *en build*, así el contenedor arranca en segundos, no minutos.

```bash
cp .env.example .env   # y cambia API_TOKEN

# levanta LOS TRES servicios (el profile "full" agrega ambas APIs; sin el flag,
# `docker compose up -d` sigue levantando solo postgres, modo dev de arriba)
docker compose --profile full up -d
docker compose --profile full ps   # espera a que "cerebro-memory-api" y "cerebro-docs-api" esten "healthy"

curl http://localhost:8005/health   # cerebro-memory (host 8005 -> contenedor 8000)
curl http://localhost:8006/health   # cerebro-docs   (host 8006 -> contenedor 8000)
```

Para reconstruir una imagen tras un cambio de código:
`docker compose --profile full build cerebro-memory-api` (o `cerebro-docs-api`).

### C. Solo quiero conectar Claude/un agente vía MCP o CLI (ya tengo las APIs corriendo en otro lado)

No necesitas clonar el backend - solo la capa de cliente:

```bash
pip install -e "packages/cerebro-clients[dev]" -e "packages/cerebro-mcp[dev]"
# o, para el CLI en vez del servidor MCP (cerebro-cli tambien necesita cerebro-memory
# instalado, solo para reusar su parser de Markdown - ver "CLI" abajo):
pip install -e "packages/cerebro-clients[dev]" -e "packages/cerebro-memory[dev]" -e "packages/cerebro-cli[dev]"
```

Ve directo a "Conectar a Claude (servidor MCP)" más abajo, apuntando
`CEREBRO_MEMORY_URL`/`CEREBRO_DOCS_URL` a las APIs ya desplegadas (modo A o B, tuyas o
de un tercero) y `CEREBRO_TOKEN` con un token de scope apropiado (ver "Seguridad" - normalmente
no querrás darle el token root a cada agente).

---

`GET /health` no requiere auth en ninguna de las dos APIs; el resto de endpoints
requieren `Authorization: Bearer <token>` (ver "Seguridad").

## Uso rapido

```bash
# --- cerebro-memory (modo A, puerto 8000 local) ---
TOKEN=change-me-dev-token

curl -s -X POST localhost:8000/contexts \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"slug":"finanzas-personales","name":"Finanzas personales","kind":"domain","description":"Gastos e ingresos personales"}'

curl -s -X POST localhost:8000/memories \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"content":"Este mes gaste 450 dolares en supermercado","context":"finanzas-personales","type":"episodic"}'

curl -s "localhost:8000/memories/search?q=cuanto+gaste+este+mes&context=finanzas-personales" \
  -H "Authorization: Bearer $TOKEN"

# --- cerebro-docs (modo A, puerto 8010 local) ---
DOCS_TOKEN=change-me-dev-token-docs

curl -s -X POST localhost:8010/categories \
  -H "Authorization: Bearer $DOCS_TOKEN" -H "Content-Type: application/json" \
  -d '{"slug":"infraestructura","name":"Infraestructura","description":"Runbooks y notas de infra"}'

curl -s -X POST localhost:8010/documents \
  -H "Authorization: Bearer $DOCS_TOKEN" -H "Content-Type: application/json" \
  -d '{"category":"infraestructura","title":"Runbook: restore de Postgres","content":"# Runbook\n\n## Pasos\n1. ..."}'

curl -s "localhost:8010/documents?q=restore+postgres" -H "Authorization: Bearer $DOCS_TOKEN"
```

En Docker (modo B), reemplaza `localhost:8000` por `localhost:8005` y
`localhost:8010` por `localhost:8006`.

## Arquitectura

```
Agente (Claude / GPT / Gemini / custom)
        |
        v
   MCP Server (stdio)  -----------------------  packages/cerebro-mcp/src/cerebro_mcp/server.py
   o CLI                -----------------------  packages/cerebro-cli/src/cerebro_cli/main.py
        |                                        ambos son adaptadores delgados, sin logica
        v                                        propia (salvo import-markdown / backup-restore)
  cerebro-clients (SDK httpx compartido)  -----  packages/cerebro-clients/src/cerebro_clients/
        |
        +----------------------------+----------------------------+
        v                                                         v
  cerebro-memory API (FastAPI)                          cerebro-docs API (FastAPI)
  packages/cerebro-memory/src/cerebro_memory/api.py      packages/cerebro-docs/src/cerebro_docs/api.py
        |                                                         |
        +--> Retrieval hibrido: vector+full-text+RRF    retrieval.py
        +--> Context Engine: scoping y desambiguacion    context_engine.py
        +--> Relaciones / grafo ligero (aristas+timeline) graph.py
        +--> Clasificador local opcional (OFF x defecto)  context_engine.py (Fase 4)
        |                                                         |
        |                                                +--> categorias + documentos
        |                                                +--> versionado (document_versions)
        |                                                +--> parches parciales por seccion
        v                                                         v
  PostgreSQL + pgvector, schema `cerebro_memory`         mismo Postgres, schema `cerebro_docs`
  (contexts, memories, audit_log, disambiguation_log,    (categories, documents, document_versions,
   context_preferences, memory_edges, api_tokens)         api_tokens)
```

Qdrant, Redis y un modelo auxiliar local corriendo por defecto **no aparecen** en el
compose a propósito ("earn your complexity"): pgvector cubre el volumen de memoria de
un usuario individual con latencias de un dígito de ms, y el punto de enchufe para un
clasificador local (Ollama) existe pero está apagado hasta que haya dataset real que
lo justifique (ver "Clasificador local opcional" abajo).

`cerebro-memory` y `cerebro-docs` comparten la misma instancia de Postgres (mismo
`DATABASE_URL`/`POSTGRES_PASSWORD`) pero cada uno vive en su propio schema y aplica
sus propias migraciones al arrancar — son servicios stateless independientes, no un
monolito partido en dos procesos que se coordinan en runtime.

## Endpoints de `cerebro-memory`

| Metodo | Ruta | Scope | Descripcion |
|---|---|---|---|
| `POST` | `/contexts` | write | crear contexto (`slug`, `name`, `kind`, `description?`) |
| `GET` | `/contexts` | read | listar contextos (filtrado a `allowed_contexts` del token, si tiene) |
| `DELETE` | `/contexts/{slug}` | admin | borra el contexto; 409 si tiene memorias salvo `?force=true` (las borra en duro junto con el contexto, cascada a sus `memory_edges`) |
| `POST` | `/memories` | write | crear memoria; `context` obligatorio; rechaza credenciales (422) |
| `GET` | `/memories/search` | read | retrieval hibrido: `q`, `context?`, `scope?` (`auto`\|`all`\|`<slug>`, default `auto`), `type?`, `limit?`, `include_superseded?`, `expand?` (default `false`). Devuelve `{results, scope_decision, related}` -- ver "Context Engine" y "Relaciones y timeline" abajo. |
| `PATCH` | `/memories/{id}` | write | crea version nueva + supersede la anterior (nunca edita in-place) |
| `DELETE` | `/memories/{id}` | write | `?hard=false` archiva (default), `?hard=true` borra en duro (cascada a sus `memory_edges`) |
| `POST` | `/memories/{id}/edges` | write | crea una arista `{to_memory, relation, note?}`; 422 si `relation` no es del vocabulario o `to_memory == id`, 404 si alguna memoria no existe, 409 si la arista ya existe |
| `DELETE` | `/memories/{id}/edges/{edge_id}` | write | borra una arista (debe tocar `id`) |
| `GET` | `/memories/{id}/related` | read | vecinos a 1 salto (ambas direcciones) + cadena de supersedencia virtual; filtro `relation?` |
| `GET` | `/timeline` | read | memorias `episodic`/`decision` ordenadas por fecha efectiva; filtros `context?`, `from?`, `to?`, `limit?=50` |
| `POST` | `/disambiguations/{id}/resolve` | write | resuelve una desambiguacion pendiente (`{"context": "<slug>"}`); hace crecer `context_preferences` |
| `GET` | `/disambiguations/export` | admin | dataset crudo para `cerebro memory export-disambiguations` |
| `GET` | `/stats` | read | memorias por contexto/estado (filtrado por `allowed_contexts`), desambiguaciones (total/auto/agent/user), preferencias aprendidas |
| `POST` | `/tokens` | admin | crea un token con scopes (`{name, scopes, allowed_contexts?}`); el valor en claro solo se devuelve en ESTA respuesta |
| `GET` | `/tokens` | admin | lista tokens (sin hashes ni valores en claro) |
| `DELETE` | `/tokens/{name}` | admin | revoca un token por nombre |
| `GET` | `/health` | ninguno | sin auth; chequea conexion a la base de datos |

Header opcional `X-Agent-Name` identifica al agente que llama (usado en `source` y en
`audit_log`; default `"unknown"`) -- **excepto con un token con nombre**, cuyo `name`
pisa siempre este header (ver "Seguridad": es la identidad real del agente, no
autodeclarada). Detalle completo de scopes y `allowed_contexts` en "Seguridad" abajo.

## Seguridad

Cuatro piezas: tokens con identidad propia, scopes, restricción por contexto (o
categoría en `cerebro-docs`), y backups. Todo excepto `GET /health` requiere
`Authorization: Bearer <token>`, en ambas APIs.

### Tokens y scopes

Dos tipos de credencial válidos en el mismo header, en cada API:

- **Token root** (`API_TOKEN` del `.env` de cada servicio): comparado byte a byte
  (`secrets.compare_digest`), tiene los tres scopes (`read`, `write`, `admin`) sobre
  todos los contextos/categorías, sin restricción. Pensado para ti mismo / el
  bootstrap inicial -- no lo repartas a agentes individuales. `cerebro-memory` y
  `cerebro-docs` tienen cada uno su propio `API_TOKEN` (ver sección "cerebro-docs" del
  `.env.example`) -- **no es el mismo valor por defecto**.
- **Tokens con nombre**, creados con `cerebro memory token create` (solo
  cerebro-memory), `cerebro docs` no tiene aún un subcomando `token` propio -- usa
  `POST /tokens` directo o el comando transversal de abajo --, o el comando
  **transversal** `cerebro token create` (registra el mismo secreto en ambos
  servicios a la vez, ver más abajo). Se guardan como su hash SHA-256 -- **el valor en
  claro se muestra una sola vez, al crearlo**, y no se puede volver a recuperar (solo
  revocar y crear uno nuevo).

```bash
# token escopado SOLO a cerebro-memory
cerebro memory token create claude-desktop --scopes read,write
cerebro memory token create agente-trabajo --scopes read --contexts cliente-acme,infraestructura
cerebro memory token list      # sin hashes ni valores en claro
cerebro memory token revoke agente-trabajo

# token TRANSVERSAL: un solo secreto, registrado en cerebro-memory Y cerebro-docs
cerebro token create claude-desktop --scopes read,write --contexts cliente-acme --categories infraestructura
cerebro token revoke claude-desktop
```

Tres scopes (mismo vocabulario en ambas APIs):

| Scope | Cubre en `cerebro-memory` | Cubre en `cerebro-docs` |
|---|---|---|
| `read` | todo `GET` (excepto `/health`) | todo `GET` (excepto `/health`) |
| `write` | `POST`/`PATCH`/`DELETE` de memorias, contextos (crear), aristas y `POST /disambiguations/{id}/resolve` | `POST`/`PATCH`/`DELETE` de documentos, categorías (crear/editar) |
| `admin` | gestión de tokens (`/tokens/*`), `GET /disambiguations/export`, `DELETE /contexts/{slug}` | gestión de tokens (`/tokens/*`), `DELETE /categories/{slug}` |

Un token puede tener varios scopes a la vez (`--scopes read,write`); `admin` **no**
implica `read`/`write` automáticamente.

### Restricción por contexto / categoría

`--contexts a,b` (cerebro-memory) o `--categories a,b` (cerebro-docs) limita un token
a un subconjunto; sin la opción, ve todos. En `cerebro-memory` se aplica en tres
sitios (búsqueda, escritura, `/stats`/`/timeline` -- ver detalle abajo). En
`cerebro-docs`, `allowed_categories` filtra `GET /categories`/`GET /documents` en
silencio y devuelve `403` en escritura o lectura directa (`GET
/documents/{categoria}/{slug}`) fuera de la lista.

Detalle de `cerebro-memory` (`allowed_contexts`):

- **Búsqueda** (`GET /memories/search`): un `context`/`scope=<slug>` explícito fuera
  de la lista es `403`. Sin contexto explícito, `scope=all` narrows silenciosamente
  los resultados al subconjunto permitido, y `scope=auto` (Context Engine) directamente
  **excluye** los contextos ajenos del scoring -- nunca pueden ganar como auto-scope
  ni aparecer como candidato "ambiguo": el token ni se entera de que existen.
- **Escritura**: crear/actualizar/borrar una memoria, arista o desambiguación en un
  contexto fuera de la lista es `403`.
- **`GET /stats` / `GET /timeline`**: filas de contextos no permitidos se omiten en
  vez de listarse; un `context` explícito fuera de la lista en `/timeline` es `403`.

### Tokens transversales

`cerebro token create <name> --scopes ... [--contexts ...] [--categories ...]`
genera **un solo secreto** y lo registra por separado en ambas APIs (`POST /tokens`
de cada una, con `value` fijado al mismo valor). Si una de las dos llamadas falla, el
secreto generado queda persistido localmente
(`packages/cerebro-cli/src/cerebro_cli/tokens.py`) hasta que ambos servicios
confirman éxito -- reintentar el mismo comando reusa el mismo secreto en vez de
generar uno nuevo, y el registro es idempotente por nombre en cada API. `cerebro
token revoke <name>` revoca en ambos servicios; un `404` en alguno (ya revocado o
nunca existió ahí) cuenta como éxito.

### Identidad de agente

El `name` de un token con nombre **pisa** cualquier `X-Agent-Name` que el cliente
mande -- queda como `memory.source`/`documents.created_by` y como `audit_log.agent`
(solo cerebro-memory) la identidad real verificada por el token, no lo que el propio
cliente diga ser. El token root no tiene identidad fija propia, así que sigue usando
`X-Agent-Name` (default `"unknown"`), en ambas APIs.

### Secretos

`POST /memories` y `PATCH /memories/{id}` (**solo en cerebro-memory**) rechazan (422)
contenido que matchee patrones de credenciales reales (claves AWS, tokens de
GitHub/Slack, API keys estilo `sk-...`, cadenas de conexión con password embebido,
asignaciones `password=...`) -- ver
`packages/cerebro-memory/src/cerebro_memory/security.py`. El mensaje de rechazo
sugiere el formato de referencia sancionado: `secret://<entorno>/<nombre>` (nunca se
almacena el valor). **`cerebro-docs` NO filtra credenciales** -- decisión explícita
(un runbook o una nota de infra a veces necesita mostrar un ejemplo de connection
string o un placeholder), ver `packages/cerebro-docs/src/cerebro_docs/api.py:8-11`.

### Validación de entrada estricta

`cerebro-docs` rechaza (422) cualquier campo desconocido en el body de sus modelos de
entrada (`StrictIn`, `extra="forbid"` -- ver
`packages/cerebro-docs/src/cerebro_docs/api.py:53-61`): un typo del cliente (p.ej.
mandar `content` en vez de `body` en un parche de sección) nunca cae en silencio al
default del campo real.

### Cifrado y backups

TLS en tránsito (termínalo con un reverse proxy delante si expones cualquier API fuera
de tu red); en reposo, cifrado de disco a nivel de VPS como línea base.

`cerebro backup` (`pg_dump` vía `docker compose`) es la pieza que falta para que
"memoria persistente" no sea una promesa vacía. Un solo Postgres compartido significa
que un solo dump cubre **ambos** schemas (`cerebro_memory` y `cerebro_docs`) en una
operación. Automatízalo:

```bash
# Windows: Task Scheduler, diario a las 3am
schtasks /create /tn "cerebro backup" /tr "D:\ruta\al\repo\.venv\Scripts\cerebro.exe backup" /sc daily /st 03:00

# Linux/Mac: cron, diario a las 3am
0 3 * * * cd /ruta/al/repo && .venv/bin/cerebro backup >> backups/backup.log 2>&1
```

Prueba el restore de verdad de vez en cuando (`cerebro restore <archivo.sql>`) -- un
backup nunca verificado no cuenta como backup. `cerebro restore` sobreescribe **ambos**
schemas y pide confirmación explícita (`--yes` para omitirla en scripts).

## Context Engine (Fase 2, `cerebro-memory`)

`GET /memories/search` decide el *scope* de la búsqueda antes de aplicar el retrieval
final. Tres modos, vía el parámetro `scope`:

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
preferencia es deliberadamente pequeño por unidad de peso
(`CONTEXT_ENGINE_PREFERENCE_BOOST_PER_WEIGHT`, default `0.008`): un solo término
genérico que colisiona legítimamente entre contextos (p.ej. "mes", "costos") no debe
poder tumbar la señal real de retrieval por una sola resolución; hace falta refuerzo
consistente.

Umbrales configurables por entorno (nombres `CONTEXT_ENGINE_*`, ver
`packages/cerebro-memory/src/cerebro_memory/config.py` para la lista completa y los
defaults calibrados contra `packages/cerebro-memory/evals/`).

`GET /stats` expone `disambiguations` (total, cuántas se resolvieron `auto`, `agent`,
`user` o `local_model` -- Fase 4, ver abajo) y `preferences_learned` (términos
aprendidos por contexto) -- es la forma más directa de ver al sistema aprender con el
uso; el servidor MCP lo expone como `memory_stats()`, y `cerebro memory stats` (CLI)
lo formatea para consola.

## Clasificador local opcional (Fase 4, `cerebro-memory`)

**OFF por defecto.** No tiene sentido entrenar ni activar un modelo local de
desambiguación mientras no exista un dataset real de ambigüedades resueltas -- hoy no
existe. Lo que esta fase construye no es el modelo, es el **punto de enchufe**: la
interfaz `AmbiguityResolver`
(`packages/cerebro-memory/src/cerebro_memory/context_engine.py`) que `decide_scope`
invoca *después* de que el scoring determinista de la Fase 2 ya decidió que un caso es
ambiguo, para intentar resolverlo localmente en vez de devolverlo al agente que llama.

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
/ `cerebro memory stats` de las resoluciones `auto` (scoring determinista) y `agent`
(agente/MCP eligiendo con el contexto de la conversación).

**Cuándo activarlo en serio**: (a) hay **≥ ~500 desambiguaciones registradas** -- usa
`cerebro memory export-disambiguations` para ver cuántas hay y exportar el dataset --
**y** (b) hay una razón medida para hacerlo (latencia, costo, o una política de
privacidad estricta de "ni la query sale del VPS"). Sin ambas condiciones, esto es
infraestructura sin usar a propósito -- earn your complexity.

Variables de entorno (`packages/cerebro-memory/src/cerebro_memory/config.py`):

| Variable | Default | Uso |
|---|---|---|
| `CONTEXT_ENGINE_RESOLVER` | `none` | `none` (NullResolver) \| `ollama` (OllamaResolver) |
| `OLLAMA_URL` | `http://localhost:11434` | base URL de la API de Ollama |
| `OLLAMA_MODEL` | `qwen2.5:1.5b` | modelo a pedirle a Ollama |

## Relaciones y timeline (Fase 3, `cerebro-memory`)

Grafo ligero **en Postgres** (`memory_edges`,
`packages/cerebro-memory/db/migrations/003_edges.sql`) -- nada de base de grafos
dedicada. Dos piezas: aristas explícitas entre memorias, y una línea de tiempo sobre
`occurred_at`.

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
incluye la cadena de supersedencia (`memories.superseded_by`, desde Fase 1) como una
relación **virtual** `"supersedes"` (`virtual: true`, `edge_id: null`) -- nunca se
escribe a `memory_edges`, se deriva en lectura. `relation=supersedes` como filtro
devuelve solo esa cadena; cualquier otro valor del vocabulario filtra solo aristas
reales.

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
accidental de vocabulario entre contextos.

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

## `cerebro-docs`: documentos Markdown versionados

Servicio hermano de `cerebro-memory`, para el otro extremo del espectro: no memorias
cortas y atómicas, sino documentos Markdown completos (runbooks, especificaciones,
notas largas) organizados en categorías, con historial de versiones y la posibilidad
de parchear una sección concreta sin reenviar el documento entero.

| Metodo | Ruta | Scope | Descripcion |
|---|---|---|---|
| `POST` | `/categories` | write | crea una categoría (`slug`, `name`, `description?`) |
| `GET` | `/categories` | read | lista categorías (filtrado a `allowed_categories` del token, si tiene) |
| `PATCH` | `/categories/{slug}` | write | renombra/edita una categoría; sus documentos no cambian de ruta lógica (el FK es `category_id`, no texto copiado) |
| `DELETE` | `/categories/{slug}` | admin | 409 si tiene documentos, salvo `?force=true` (cascada a documentos y su historial de versiones) |
| `POST` | `/documents` | write | crea un documento (`title`, `content`, `category`, `slug?`); 409 si el slug ya existe en esa categoría |
| `GET` | `/documents/{category}/{slug}` | read | lee un documento por su ruta exacta |
| `GET` | `/documents` | read | lista documentos, `updated_at desc`; filtros `category?`, `q?` (full-text simple), `limit?=20`, `offset?=0` |
| `PATCH` | `/documents/{id}` | write | reemplazo completo (incluye mover de categoría); snapshotea la versión anterior en `document_versions` antes de escribir |
| `PATCH` | `/documents/{id}/section` | write | parche parcial por heading: `operation` en `replace`\|`append`\|`insert_after`\|`insert_before`\|`delete`; snapshotea igual que el reemplazo completo |
| `DELETE` | `/documents/{id}` | write | borra el documento (cascada a `document_versions`) |
| `POST` | `/tokens` | admin | crea un token con scopes (`{name, scopes, allowed_categories?}`) |
| `GET` | `/tokens` | admin | lista tokens |
| `DELETE` | `/tokens/{name}` | admin | revoca un token por nombre |
| `GET` | `/stats` | read | conteos de categorías/documentos/versiones (mirror mínimo del `/stats` de cerebro-memory, sin desambiguaciones ni preferencias) |
| `GET` | `/health` | ninguno | sin auth; chequea conexion a la base de datos |

**Sección = desde un heading hasta el siguiente del mismo nivel o superior.** Si el
heading buscado aparece más de una vez, `PATCH /documents/{id}/section` devuelve `409`
(ambiguo, nunca adivina cuál); si no existe, `404` a menos que se pase
`create_if_missing=true` (crea la sección al final, con `new_heading_level`, default
`2`).

```bash
curl -s -X PATCH localhost:8010/documents/$DOC_ID/section \
  -H "Authorization: Bearer $DOCS_TOKEN" -H "Content-Type: application/json" \
  -d '{"heading":"## Pasos","operation":"append","body":"4. Verificar healthcheck"}'
```

`cerebro-docs` **no filtra credenciales** en el contenido (a diferencia de
`POST /memories` en cerebro-memory) y **rechaza campos desconocidos** en cualquier
body de entrada (`422`, ver "Validación de entrada estricta" arriba). No tiene
retrieval semántico ni Context Engine -- la búsqueda (`GET /documents?q=...`) es
full-text simple (`websearch_to_tsquery('simple', ...)` + `ts_rank`), siempre
parametrizada.

## Tests

```bash
# cada paquete tiene su propia suite (testpaths = ["tests"] en su pyproject.toml)
cd packages/cerebro-memory && pytest
cd packages/cerebro-docs && pytest
cd packages/cerebro-clients && pytest
cd packages/cerebro-mcp && pytest
cd packages/cerebro-cli && pytest
```

En `cerebro-memory`: `tests/test_rrf.py`, `tests/test_security.py`,
`tests/test_context_engine.py`, la parte unitaria de `tests/test_auth.py`
(`Principal`, `hash_token`/`generate_token`) y la parte unitaria de
`tests/test_graph.py` (vocabulario de relaciones) son unitarios (sin base de datos).
`tests/test_supersedence.py`, la parte de integracion de `tests/test_auth.py` (ciclo
de vida de tokens, enforcement de scopes y de `allowed_contexts`, `DELETE
/contexts/{slug}`) y la parte de integracion de `tests/test_graph.py` (aristas,
no-duplicados, direccion en `related`, cascada de hard-delete, ordering de
`timeline`) se saltan automaticamente si `DATABASE_URL` no es alcanzable (arranca
`docker compose up -d` primero desde la raíz del repo).

En `cerebro-docs`: `tests/test_auth.py`, `tests/test_documents.py`,
`tests/test_sections.py`, `tests/test_slugs.py`, `tests/test_strict_input.py` -- mismo
criterio, la parte de integración necesita `DATABASE_URL` alcanzable.

## Conectar a Claude (servidor MCP: `cerebro-mcp`)

`packages/cerebro-mcp/src/cerebro_mcp/server.py` expone **ambas** APIs como un único
servidor MCP por stdio (SDK oficial `mcp`, `FastMCP`). Es un adaptador delgado: cada
tool llama a la API HTTP correspondiente vía `cerebro_clients` (`MemoryClient` /
`DocsClient`), sin lógica de negocio propia -- toda vive en las APIs, así
`cerebro-cli` comparte exactamente el mismo camino.

19 tools disponibles:

- **`memory_*`** (10, hablan con `cerebro-memory`): `memory_search`,
  `memory_remember`, `memory_update`, `memory_forget`, `memory_contexts`,
  `memory_create_context`, `memory_stats`, `memory_link`, `memory_related`,
  `memory_timeline`.
- **`docs_*`** (9, hablan con `cerebro-docs`): `docs_create_category`,
  `docs_categories`, `docs_save`, `docs_get`, `docs_search`, `docs_list`,
  `docs_update`, `docs_patch_section`, `docs_delete`.

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
timeline" arriba.

`memory_link(from_memory_id, to_memory_id, relation, note?)` crea una arista explícita
entre dos memorias (vocabulario: `relates_to`, `caused_by`, `part_of`, `contradicts`,
`follows`); su docstring explica cuándo usar cada una (decisiones→causas,
procedimientos→proyectos, episodios→consecuencias). `memory_related(memory_id,
relation?)` lista los vecinos a 1 salto, incluida la cadena de supersedencia virtual.
`memory_timeline(context?, from_date?, to_date?, limit?)` responde preguntas tipo
"¿qué pasó en X las últimas semanas?".

`docs_save(category, title, content, slug?)` crea un documento nuevo.
`docs_patch_section(document_id, heading, operation, body?, create_if_missing?,
new_heading_level?)` parchea una sección puntual sin reenviar el documento completo --
el uso previsto para que un agente actualice, p.ej., un runbook línea por línea en vez
de reescribirlo entero cada vez. `docs_search(query, category?, limit?, offset?)` hace
full-text simple; `docs_list`/`docs_categories` listan sin query.

Tras instalar `cerebro-mcp` (`pip install -e packages/cerebro-mcp`) queda disponible
el entry point de consola `cerebro-mcp` (ver `[project.scripts]` en su
`pyproject.toml`). Requiere que **ambas** APIs estén corriendo
(`python -m cerebro_memory.main` y `python -m cerebro_docs.main`, o el equivalente en
Docker).

Variables de entorno que lee el servidor MCP (vía `cerebro_clients.config`):

| Variable | Default | Uso |
|---|---|---|
| `CEREBRO_MEMORY_URL` | `http://localhost:8005` | base URL de `cerebro-memory` |
| `CEREBRO_DOCS_URL` | `http://localhost:8010` | base URL de `cerebro-docs` |
| `CEREBRO_TOKEN` | *(vacío)* | token compartido para ambas APIs |
| `CEREBRO_AGENT_NAME` | `cerebro-client` | identidad enviada como `X-Agent-Name` (audit log, `memory.source`/`documents.created_by`) |
| `KNOWLEDGEOS_API_URL` / `KNOWLEDGEOS_API_TOKEN` / `KNOWLEDGEOS_AGENT_NAME` | *(fallback)* | legado, **solo aplica a `cerebro-memory`**; si ya los tenías configurados de antes de la migración siguen funcionando |

Nota sobre los defaults: `CEREBRO_MEMORY_URL` por defecto asume el puerto de Docker
(`8005`), mientras que `CEREBRO_DOCS_URL` por defecto asume el puerto de desarrollo
local sin Docker (`8010`, no `8006`). Si corres ambas APIs con el mismo modo (A o B),
exporta explícitamente las dos variables para que apunten al mismo lado -- ver
"Quickstart" arriba para los pares de puertos de cada modo.

### Claude Code

```bash
claude mcp add cerebro --scope user \
  -e CEREBRO_MEMORY_URL=http://localhost:8005 \
  -e CEREBRO_DOCS_URL=http://localhost:8006 \
  -e CEREBRO_TOKEN=change-me-dev-token \
  -e CEREBRO_AGENT_NAME=claude-code \
  -- cerebro-mcp
```

### Claude Desktop

Agrega esto a `claude_desktop_config.json` (menú Claude > Settings > Developer > Edit
Config):

```json
{
  "mcpServers": {
    "cerebro": {
      "command": "D:\\dev\\jobs\\luisjdev\\cerebro\\.venv\\Scripts\\cerebro-mcp.exe",
      "env": {
        "CEREBRO_MEMORY_URL": "http://localhost:8005",
        "CEREBRO_DOCS_URL": "http://localhost:8006",
        "CEREBRO_TOKEN": "change-me-dev-token",
        "CEREBRO_AGENT_NAME": "claude-desktop"
      }
    }
  }
}
```

Si `cerebro-mcp` no está en el `PATH` que ve Claude Desktop, usa la ruta absoluta al
ejecutable del venv, p.ej. en Windows:
`"command": "D:\\ruta\\al\\repo\\.venv\\Scripts\\cerebro-mcp.exe"`.

## CLI (`cerebro`)

`packages/cerebro-cli/src/cerebro_cli/main.py` (entry point de consola `cerebro`,
instalado por `pip install -e packages/cerebro-cli`) es un cliente delgado de ambas
APIs HTTP vía `cerebro_clients` -- igual que el servidor MCP, no tiene lógica de
negocio propia (salvo la orquestación del importador de Markdown, heredada de
`cerebro_memory`, y el manejo de fallo parcial de los tokens transversales, ver
"Seguridad" arriba). Antes de despachar cualquier subcomando, `main()` carga
`.env.production`/`.env` de la raíz del monorepo sin pisar variables ya presentes en
el entorno (`packages/cerebro-cli/src/cerebro_cli/dotenv.py`) -- así `cerebro memory
stats` sigue hablando con el VPS de producción por defecto si ese archivo apunta ahí,
sin depender de un wrapper de shell hecho a mano.

```bash
cerebro --help
```

Tres grupos de subcomandos: `cerebro memory ...`, `cerebro docs ...`, y comandos
transversales sin prefijo.

### `cerebro memory ...`

```bash
cerebro memory stats                                              # igual que GET /stats de cerebro-memory
cerebro memory export-disambiguations --output disambiguations.jsonl
cerebro memory export-disambiguations --resolved-only

# tokens ESCOPADOS solo a cerebro-memory - requiere auth admin
cerebro memory token create claude-desktop --scopes read,write
cerebro memory token create agente-trabajo --scopes read --contexts cliente-acme,infraestructura
cerebro memory token list
cerebro memory token revoke agente-trabajo
```

`export-disambiguations` siempre imprime cuántos ejemplos hay frente al umbral del
plan (`~500`, ver "Clasificador local opcional" arriba) para que sea fácil saber si ya
vale la pena considerar el fine-tuning.

#### Importar memorias existentes (Fase 5)

`cerebro memory import-markdown` es el **primer conector de Fase 5**: importa
archivos Markdown de memoria ya existentes (`MEMORY.md`/`CLAUDE.md` estilo Claude
Code, o notas sueltas) como memorias de `cerebro-memory`. Se eligió como conector #1
a propósito porque resuelve la migración desde el statu quo del usuario, no porque sea
técnicamente lo más interesante.

El parsing (`packages/cerebro-memory/src/cerebro_memory/markdown_importer.py`, un
parser puro que `cerebro-cli` reusa sin depender del `cli.py`/`mcp_server.py`
originales -- ya eliminados de `cerebro-memory`) reconoce tres formatos, en este
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
volcado de código fuente.

```bash
# vista previa: que se importaria, sin escribir nada
cerebro memory import-markdown ./mis-notas --context notas-personales --dry-run

# import real; crea el contexto si no existe
cerebro memory import-markdown ./mis-notas \
  --context notas-personales --create-context \
  --context-description "Notas migradas desde Markdown"

# un solo archivo, tipo forzado
cerebro memory import-markdown ./MEMORY.md --context notas-personales --type semantic
```

Antes de insertar cada memoria, el importador busca por similitud (`GET
/memories/search` acotado al contexto destino) usando el propio contenido como query;
si el resultado top tiene un score de RRF alto **y** el mismo título exacto, la salta
y la reporta como "duplicada" en vez de reinsertarla -- así una segunda corrida sobre
el mismo directorio (o un `MEMORY.md` que enlaza archivos que el glob recursivo ya
recorrió por separado) no duplica memorias. Credenciales detectadas por la API
(`POST /memories` -> 422) se capturan y reportan como "rechazada" sin interrumpir el
resto del import. Al final imprime un resumen: `N importadas, M duplicadas
(saltadas), K rechazadas`.

### `cerebro docs ...`

```bash
cerebro docs category create infraestructura --name "Infraestructura" --description "Runbooks y notas de infra"
cerebro docs category list
cerebro docs category rename infraestructura infra --name "Infra"
cerebro docs category delete infra --force

cerebro docs save infraestructura "Runbook: restore de Postgres" --content-file runbook.md
cerebro docs get infraestructura runbook-restore-de-postgres
cerebro docs list --category infraestructura --limit 10
cerebro docs search "restore postgres"

cerebro docs update $DOC_ID "Runbook: restore de Postgres (v2)" infraestructura --content-file runbook-v2.md
cerebro docs patch-section $DOC_ID "## Pasos" append --body "4. Verificar healthcheck" --create-if-missing

cerebro docs delete $DOC_ID --yes
cerebro docs stats
```

`--content-file` es opcional en `save`/`update` -- si se omite, el CLI lee el
contenido de stdin (útil para pipear la salida de otro comando o un heredoc).

### Comandos transversales (sin prefijo)

```bash
# backup / restore (pg_dump / psql via docker compose) - cubre AMBOS schemas
cerebro backup --output backups/
cerebro restore backups/cerebro-20260812-030000.sql   # pide confirmacion (DESTRUCTIVO)
cerebro restore backups/cerebro-20260812-030000.sql --yes   # sin confirmar

# tokens TRANSVERSALES (un secreto, registrado en cerebro-memory y cerebro-docs)
cerebro token create claude-desktop --scopes read,write --contexts cliente-acme --categories infraestructura
cerebro token revoke claude-desktop
```

`cerebro token create` imprime el token en claro **una sola vez** -- guárdalo de
inmediato (p.ej. como `CEREBRO_TOKEN` del cliente MCP correspondiente). Ver "Tokens
transversales" en la sección "Seguridad" arriba para el comportamiento ante
fallo parcial.

## Evaluación

La suite de evaluación de retrieval de `cerebro-memory`
(`packages/cerebro-memory/evals/`, ver `packages/cerebro-memory/evals/README.md` para
el detalle completo de métricas y corpus) mide precision@k, recall@k y tasa de
contaminación entre contextos, con un corpus sintético de ~40 memorias en 6 contextos
y 30 casos de prueba en español. `cerebro-docs` no tiene una suite de evaluación
equivalente (no hace retrieval semántico ni scoping, solo full-text simple).

```bash
cd packages/cerebro-memory

# baseline: overlap de palabras clave, sin nocion de contexto
python evals/harness/run_eval.py --adapter naive

# cerebro-memory real, vía la API HTTP (requiere la API corriendo y Postgres arriba)
python -m cerebro_memory.main &   # o en otra terminal

# control (Fase 1): retrieval hibrido sin Context Engine
KNOWLEDGEOS_SEARCH_SCOPE=all python evals/harness/run_eval.py --adapter cerebro-memory --include-superseded

# Context Engine (Fase 2): scope=auto
KNOWLEDGEOS_SEARCH_SCOPE=auto python evals/harness/run_eval.py --adapter cerebro-memory --include-superseded
```

(la variable de entorno conserva su nombre legado `KNOWLEDGEOS_SEARCH_SCOPE` -- el
harness de `evals/` no se tocó en la migración al monorepo, solo se movió de sitio.)

`evals/harness/adapters/` habla con la API real por HTTP (igual que lo haría el
servidor MCP): en `setup()` verifica `/health`, crea los contextos del corpus que
falten y purga memorias de corridas anteriores.

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

Umbrales calibrados en `packages/cerebro-memory/src/cerebro_memory/config.py`
(`CONTEXT_ENGINE_*`); entre corridas del benchmark, trunca `disambiguation_log` y
`context_preferences` para medir `scope=auto` en frío (sin aprendizaje acumulado de
una corrida anterior).

Lee `packages/cerebro-memory/evals/README.md` para cómo agregar casos o corpus
propios, y `--include-superseded` para que la categoría `temporal` sea significativa.

Estos números vienen de antes de la migración al monorepo y de la separación de
`cerebro-docs`; ninguno de los dos cambios toca `cerebro-memory/retrieval.py`,
`context_engine.py` ni el corpus de `evals/`, así que se mantienen como línea base
hasta la próxima recalibración.

## Estructura

```
compose.yaml                  # postgres (siempre) + cerebro-memory-api + cerebro-docs-api (profile "full")
.env.example                  # variables compartidas: DATABASE_URL, API_TOKEN, APP_PORT, EMBEDDING_*, CONTEXT_ENGINE_*, seccion cerebro-docs
.env.production                # (no versionado) config de produccion que cerebro-cli carga automaticamente
plan_v2.md                     # arquitectura y modelo de datos original de cerebro-memory (Fases 1-4)
packages/
    cerebro-memory/            # servicio API puro: memoria persistente
        Dockerfile              # imagen multi-stage, no-root, pre-descarga el modelo en build
        pyproject.toml          # sin [project.scripts]: no expone CLI ni MCP propios
        db/migrations/           # 001_init .. 005_schema_cerebro_memory
        evals/                    # suite de evaluacion de retrieval (ver "Evaluacion")
        src/cerebro_memory/
            config.py             # settings desde env, incluye CONTEXT_ENGINE_*
            db.py                 # pool asyncpg + aplicacion de migraciones al arrancar
            embeddings.py         # EmbeddingProvider (fastembed local)
            security.py           # deteccion de credenciales en remember()
            auth.py                # Principal, scopes, hash de tokens, CRUD de api_tokens
            retrieval.py           # busqueda hibrida (vector + full-text) fusionada con RRF
            context_engine.py      # Context Engine + AmbiguityResolver/NullResolver/OllamaResolver
            graph.py                # aristas (memory_edges), related() 1-hop, timeline, expand de search
            api.py                  # FastAPI app (auth, scopes, CRUD, search, disambiguations, stats, edges, timeline, tokens)
            markdown_importer.py    # parsing puro, reusado por cerebro-cli (Fase 5)
            main.py                  # uvicorn entrypoint
        tests/
    cerebro-docs/               # servicio API puro: documentos Markdown versionados
        Dockerfile
        pyproject.toml            # [project.scripts]: cerebro-docs (uvicorn entrypoint, no CLI de usuario)
        db/migrations/001_init.sql
        src/cerebro_docs/
            config.py               # espejo minimo de cerebro_memory.config, sin embeddings/Context Engine
            db.py / auth.py
            slugs.py                 # slugify() para documentos/categorias
            sections.py               # apply_section_patch(): replace/append/insert_after/insert_before/delete
            api.py                     # FastAPI app: categorias, documentos versionados, tokens, stats
            main.py                     # uvicorn entrypoint
        tests/
    cerebro-clients/             # SDK httpx compartido, sin entry points (libreria)
        src/cerebro_clients/
            base.py                   # excepciones + cliente HTTP base
            config.py                  # resolucion de CEREBRO_MEMORY_URL/CEREBRO_DOCS_URL/CEREBRO_TOKEN/CEREBRO_AGENT_NAME
            memory_client.py             # MemoryClient
            docs_client.py                # DocsClient
        tests/
    cerebro-mcp/                  # servidor MCP stdio unico (FastMCP): memory_* + docs_*
        pyproject.toml              # [project.scripts]: cerebro-mcp
        src/cerebro_mcp/server.py
        tests/
    cerebro-cli/                   # CLI unico: cerebro memory / cerebro docs / backup|restore|token transversal
        pyproject.toml               # [project.scripts]: cerebro
        src/cerebro_cli/
            main.py                    # build_parser(), carga .env.production/.env antes de despachar
            dotenv.py                    # parser minimo de .env, sin pisar el entorno ya presente
            tokens.py                     # generacion/persistencia local de secretos transversales pendientes
            memory_commands.py             # cerebro memory ...
            docs_commands.py                # cerebro docs ...
            shared_commands.py               # backup, restore, token create/revoke transversal
        tests/
```
