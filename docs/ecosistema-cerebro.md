# Ecosistema cerebro — Definición

> Estado: **implementado en local el 2026-08-12** (fases 1–6 del §19 completas:
> monorepo, migración de schema, cerebro-docs, clients/MCP/CLI, compose único y gate
> de auditoría §15 aprobado — 376 tests en verde). Ejecutado por Claude como
> orquestador con subagentes de implementación, por decisión de Jose Luis.
> **Pendiente**: despliegue de la nueva versión al VPS (con pg_dump previo) y
> migración de la config MCP de los agentes a `cerebro-mcp`. Este
> documento reemplaza y absorbe a `cerebro-docs.md` (definición previa, ahora
> incompleta) — cubre el ecosistema completo. Definido el 2026-08-11 en conversación
> con Jose Luis Ortiz Sánchez; corregido el 2026-08-12 (referencias cruzadas,
> hallazgos pendientes incorporados: backup pre-migración, `GET /health`,
> transaccionalidad del snapshot, fallo parcial de tokens, backlog ampliado).

## 1. Visión general

**cerebro** deja de ser una sola herramienta y pasa a ser un ecosistema de módulos
que comparten repo, despliegue y capas de cliente, pero no dominio ni base de datos
entre sí:

- **cerebro-memory** (rename de `knowledgeos`): memoria persistente de hechos,
  decisiones y eventos atómicos — retrieval híbrido, Context Engine, grafo,
  supersedencia. Ya existe y está en uso personal real (tiene datos).
- **cerebro-docs** (nuevo): repositorio de documentos Markdown completos,
  categorizables y con CRUD, explícitamente **no memoria formal**.
- Capas de cliente compartidas entre ambos: **cerebro-clients** (SDK), **cerebro-mcp**
  (servidor MCP único), **cerebro-cli** (CLI único).

## 2. Motivación de cerebro-docs (por qué no es memoria)

cerebro-memory está diseñado alrededor de **"memory over conversation"** (se guarda
conocimiento destilado, no documentos completos — su importador de Markdown trunca
bloques de código >30 líneas porque "una memoria es un resumen destilado, no un
volcado de la fuente") y **"earn your complexity"** (ninguna capa nueva sin
justificación medida). Forzar documentos completos ahí rompería el modelo de
retrieval (RRF+vector calibrado para memorias cortas), contaminaría el Context Engine,
y no hay modelo de versionado por archivo en `memories` (la supersedencia es por
hecho). Por eso cerebro-docs es un componente separado, no una feature de
cerebro-memory.

**Es**: CRUD de documentos Markdown completos, direccionables, categorizables y
buscables vía MCP/CLI, como componente independiente del ecosistema.

**No es**: memoria formal, sistema de retrieval semántico/vectorial, control de
versiones tipo git con diffs navegables, ni almacén de binarios/adjuntos.

**Alcance de contenido, decidido explícitamente**: cerebro-docs **solo acepta
Markdown de texto** — nada de imágenes, PDFs ni otros formatos de archivo (si un
documento referencia una imagen, es como link/ruta de texto dentro del markdown, el
archivo en sí no vive en cerebro-docs). Dentro de ese límite de formato, **no hay
ningún filtro de contenido**: a diferencia de `POST /memories` en cerebro-memory (que
rechaza credenciales detectadas por `security.py`), cerebro-docs guarda lo que sea —
es una decisión explícita de Jose Luis, no un descuido. Ver §9 para la implicación de
seguridad que esto tiene sobre backups.

## 3. Repositorio: monorepo (no polyrepo)

Decisión explícita, revisando la asunción inicial de repos separados: **monorepo**
para todo el ecosistema. Motivo — el objetivo declarado era compartir despliegue
(`docker-compose`); con polyrepo eso exige un repo "umbrella" adicional con `include:`
y asumir checkouts hermanos en disco, complejidad que solo existe para sostener la
separación de repos, no para resolver un problema real. Con monorepo el
`compose.yaml` es uno solo en la raíz, sin trucos, y cada paquete sigue siendo
independientemente construible/desplegable (Dockerfile propio por paquete).

El único costo de monorepo (versionado mezclado entre módulos que evolucionan a
ritmos distintos) no aplica: no hay terceros consumiendo estos repos ni CI que
dependa de tags separados. Si algún día se necesita abrir/separar un módulo, es
mecánico (`git filter-repo`/`git subtree split`) — se paga esa complejidad solo si
llega a hacer falta.

## 4. Estructura de paquetes

```
cerebro/                          (repo único; hoy es el repo de knowledgeos)
├── compose.yaml                  # postgres compartido (schemas cerebro_memory + cerebro_docs)
│                                  # + servicio API de cada módulo (profile "full", igual patrón actual)
└── packages/
    ├── cerebro-memory/           # backend: API HTTP + schema Postgres propio (rename de knowledgeos)
    │   ├── pyproject.toml
    │   ├── src/
    │   ├── db/migrations/
    │   └── Dockerfile
    ├── cerebro-docs/             # backend: API HTTP + schema Postgres propio (nuevo)
    │   ├── pyproject.toml
    │   ├── src/
    │   ├── db/migrations/
    │   └── Dockerfile
    ├── cerebro-clients/          # SDK delgado compartido: MemoryClient, DocsClient (wrappers httpx)
    ├── cerebro-mcp/              # servidor MCP único (stdio) -- usa cerebro-clients
    └── cerebro-cli/              # CLI único (`cerebro ...`) -- usa cerebro-clients
```

`cerebro-mcp` y `cerebro-cli` **no** corren en Docker/compose — son clientes delgados
que se instalan en el host (igual que hoy `knowledgeos-mcp`/`knowledgeos` vía
`pip install -e ".[dev]"`) y hablan por HTTP a las APIs, dondequiera que estén
desplegadas.

## 5. Rename: `knowledgeos` → `cerebro-memory`

Confirmado: el paquete/CLI/servidor MCP que hoy se llama `knowledgeos` se renombra a
`cerebro-memory` como parte de esta restructuración. (Actualización 2026-08-12: el
rename y el movimiento de carpetas los ejecuta Claude como orquestador de la
implementación, por decisión de Jose Luis — la definición original preveía que los
hiciera Jose Luis a mano.)

**Punto de atención para la ejecución** (no resuelto aquí, solo señalado): las tablas
de cerebro-memory ya tienen datos reales de uso personal. Al pasar a "Postgres
compartido con schemas separados" (§8), esas tablas existentes deben moverse al
schema `cerebro_memory` (`ALTER TABLE ... SET SCHEMA cerebro_memory`, y ajustar
`search_path`/queries si están cableadas a `public`) — cuidar de no perder datos ya
guardados durante esa migración.

## 6. Modelo de datos de cerebro-docs

**Categorías, como los `contexts` de cerebro-memory** (revisión de la decisión
anterior de "texto libre autocreado"): tabla formal, creación explícita
(`docs_create_category`, mismo flujo que `memory_create_context`), y **redistribuible**
— renombrar una categoría no debe tocar los documentos que contiene.

```
categories
  id            uuid pk
  slug          text unique      -- p.ej. "ecosistema"
  name          text
  description   text nullable
  created_at    timestamptz

documents
  id              uuid pk
  category_id     uuid fk -> categories.id     -- NO texto duplicado
  slug            text            -- autogenerado del título si no se pasa
  title           text
  content         text            -- markdown completo, SIN truncar
  created_at      timestamptz
  updated_at      timestamptz
  created_by      text
  UNIQUE (category_id, slug)

document_versions
  id             uuid pk
  document_id    uuid fk -> documents.id
  content        text          -- snapshot completo antes del update/patch
  title          text
  category_id    uuid          -- categoría en el momento del snapshot
  version_number int
  created_at     timestamptz
```

**Por qué `category_id` (FK) y no texto copiado en cada documento**: es la misma
razón por la que cerebro-memory dice "`contexts` es tabla, no tag" — si la categoría
fuera un string repetido en cada fila de `documents`, renombrarla exigiría un UPDATE
masivo (y arriesga colisiones a mitad de la operación). Con FK, `PATCH
/categories/{slug}` para renombrar es **una sola fila actualizada**, y todas las rutas
de los documentos de esa categoría cambian "gratis" al resolver el join — sin tocar
`documents` en absoluto.

**Ruta canónica**: `/{categoria.slug}/{documento.slug}` (ej.
`/ecosistema/plan-cerebro-docs`), resuelta por join en tiempo de lectura, nunca
almacenada como string plano.

**Colisión de slugs — política decidida**: `UNIQUE (category_id, slug)` a nivel de
base de datos; a nivel de API, si `docs_save` genera un slug (del título) que ya existe
en esa categoría, **falla con un error explícito** que apunta al documento existente
(sugiriendo `docs_update` si la intención era editarlo) — nunca auto-sufija
(`-2`, `-3`) ni sobrescribe en silencio. Es el mismo criterio conservador que ya
aplicamos en headings duplicados (§12) y en el patrón general de "nunca editar
in-place sin que se pida explícitamente" (§6 de la versión anterior de este
documento). Mover un documento a otra categoría (cambiar su `category_id` vía
`docs_update`) pasa por la misma validación de unicidad en la categoría destino.

**Subcategorías/jerarquía**: **no**, quedan planas por ahora — mismo criterio que
`contexts` en cerebro-memory (tampoco tiene jerarquía). Justificación: jerarquía real
implica renombrar/mover recursivo, rutas multi-segmento, y filtrado por prefijo en vez
de igualdad exacta — nada de eso está justificado con el volumen de categorías que un
uso personal genera. Si algún día hacen falta docenas de categorías y se necesita
agrupar, se puede lograr con convención de nombres (`ecosistema-cerebro`,
`ecosistema-docs`) sin estructura formal. Revisar solo si esto duele de verdad en la
práctica, no antes.

## 7. API HTTP de cerebro-docs

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/categories` | crear categoría (`slug`, `name`, `description?`) |
| `GET` | `/categories` | listar categorías existentes |
| `PATCH` | `/categories/{slug}` | renombrar/editar `name`/`description`/`slug` — no toca `documents` |
| `DELETE` | `/categories/{slug}` | 409 si tiene documentos salvo `?force=true` (cascada a documentos + sus versiones) |
| `POST` | `/documents` | crear (`title`, `content`, `category` [slug existente, obligatorio], `slug?`) |
| `GET` | `/documents/{category}/{slug}` | leer por ruta exacta |
| `GET` | `/documents?category=&q=&limit=20&offset=0` | listar/filtrar; `q` = full-text simple (sin embeddings) |
| `GET` | `/health` | health check sin auth — requisito del patrón de compose (§8), que espera al servicio "healthy"; mismo contrato que el `/health` de cerebro-memory |
| `PATCH` | `/documents/{id}` | reemplazo completo (incluye mover de categoría); archiva versión anterior primero |
| `PATCH` | `/documents/{id}/section` | parche parcial por encabezado (ver §11) |
| `DELETE` | `/documents/{id}` | borrar |

`POST /documents` con una `category` que no existe → 404 con mensaje explícito
("categoría inexistente, créala primero con `docs_create_category`") — mismo mensaje
que cerebro-memory ya da hoy para contexto inexistente en `memory_remember`.

Auth por token con scopes (`read`/`write`/`admin`) igual patrón que cerebro-memory,
ver §13 (tokens transversales).

**Paginación**: `limit` (default 20, máx 100) + `offset` (default 0) en
`/documents` y en `docs_list`/`docs_search`. Se agrega porque, a diferencia de
memoria (donde casi nunca se listan todos los hechos), listar documentos completos por
categoría es un caso de uso real y esperable con el tiempo.

## 8. Despliegue compartido

- **Un solo `compose.yaml`** en la raíz del monorepo: Postgres (siempre) + servicios
  `cerebro-memory-api` y `cerebro-docs-api` (bajo el profile "full", igual patrón que
  hoy usa `docker compose --profile full`).
- **Un Postgres, dos schemas**: `cerebro_memory` y `cerebro_docs` en la misma
  instancia — aislamiento lógico completo sin duplicar infraestructura en el VPS.
- **Nunca el mismo contenedor** para dos servicios distintos — cada API es su propio
  contenedor, aunque compartan compose y Postgres.

## 9. Backup/restore: reproducibilidad de datos al 100% (no del entorno)

Alcance aclarado explícitamente: "reproducible al 100%" se refiere a los **datos**,
no a poder levantar el proyecto completo en un entorno nuevo sin ningún paso manual.
Config/secretos de despliegue quedan fuera de alcance a propósito — no es un gap por
cerrar, es una frontera de alcance decidida.

Con eso aclarado, el mecanismo actual (`knowledgeos backup`, pg_dump vía docker
compose) **ya cumple sin cambios**: como cerebro-memory y cerebro-docs comparten una
sola instancia de Postgres (§8), un solo pg_dump de esa instancia captura ambos
schemas completos — `memories`, `contexts`, `memory_edges`, `audit_log`,
`disambiguation_log`, `context_preferences`, `api_tokens` (hashes), y tras esta
definición `categories`/`documents`/`document_versions` — en una sola operación, sin
tratamiento especial por módulo. No hace falta ningún bundle, formato nuevo, ni
copiar `.env`.

**Explícitamente fuera de "backup" (correcto que quede fuera)**:
- `.env` de cada servicio (secretos de despliegue) — se re-crea manualmente al
  desplegar en un entorno nuevo, es configuración, no dato.
- Valor en claro de tokens con nombre — irrecuperable por diseño (solo se guarda el
  hash); se re-emiten con `cerebro token create` tras un restore si hace falta.
- Configuración de clientes MCP (Claude Desktop/Code) — vive fuera del proyecto, se
  reconfigura manualmente.

`cerebro restore` sigue siendo el mismo mecanismo de hoy (`psql` vía docker compose),
aplicado naturalmente a ambos schemas a la vez — no requiere ningún cambio de código
más allá de lo que ya hace.

**Implicación de seguridad que sigue vigente** (independiente de esta aclaración):
como cerebro-docs no filtra contenido (§2), un documento puede contener secretos
reales pegados por error, y ese contenido queda en el dump en texto plano — el
archivo de backup sigue siendo material sensible (cifrado en reposo a nivel de VPS,
ya es la línea base documentada en cerebro-memory).

## 10. Servidor MCP único (`cerebro-mcp`)

Un solo proceso stdio expone ambas familias de tools (prefijos `memory_*` / `docs_*`
para no confundirse). Es válido unificarlo porque el servidor MCP nunca tuvo lógica
de negocio propia — es un adaptador delgado sobre HTTP; que hable con dos APIs en vez
de una no acopla a los backends entre sí (siguen sin conocerse, sin esquema
compartido). El acoplamiento queda contenido en la capa de presentación, que es el
lugar correcto para pagarlo.

Costo aceptado: blast radius de fallas — un bug en las tools de docs podría tumbar
también las de memory a mitad de conversación. Aceptable para un ecosistema personal;
se separaría si esto llegara a ser multiusuario/alta disponibilidad.

Tools: `memory_search`, `memory_remember`, `memory_update`, `memory_forget`,
`memory_contexts`, `memory_create_context`, `memory_stats`, `memory_link`,
`memory_related`, `memory_timeline` (ya existentes) + `docs_create_category`,
`docs_categories`, `docs_save`, `docs_get`, `docs_search`, `docs_list`,
`docs_update`, `docs_patch_section`, `docs_delete` (nuevas, ver §12).

## 11. CLI único (`cerebro-cli`, comando `cerebro`)

Mismo razonamiento que el MCP: un solo binario con subcomandos por módulo, más
comandos de nivel ecosistema sin prefijo:

```
cerebro memory stats
cerebro memory token create claude-code --scopes read,write
cerebro memory import-markdown ./notas --context notas-personales
cerebro docs stats
cerebro docs category create ecosistema --description "..."
cerebro docs category rename ecosistema-cerebro ecosistema
cerebro backup            # pg_dump de ambos schemas, sin .env (reproduce datos, no entorno; ver §9)
cerebro restore <archivo>
cerebro token create <nombre> --scopes ...   # transversal, ver §12
cerebro token revoke <nombre>                # transversal, ver §12
```

## 12. Documentos y parches parciales por sección (cerebro-docs)

Tools/endpoints de escritura: `docs_create_category`, `docs_save` (crear, requiere
categoría existente), `docs_update` (reemplazo completo, incluye mover de categoría),
`docs_patch_section` (parche parcial), `docs_delete`.
Lectura: `docs_get` (ruta exacta `/categoria/documento`), `docs_search` (full-text
difuso, para referencias imprecisas como "el documento del desarrollo x"),
`docs_list`, `docs_categories`.

**`docs_patch_section`**: sección = desde un heading hasta el siguiente heading del
mismo nivel o superior (reutiliza la lógica de parseo de headings de
`markdown_importer.py`, por consistencia — sin extraerla a un paquete compartido,
es demasiado pequeña para justificar esa coordinación).
- `operation`: `replace`, `append`, `insert_after`, `insert_before`, `delete`.
- Heading no encontrado → error, salvo `create_if_missing=true`.
- Headings duplicados (ambiguos) → error explícito, nunca adivina — mismo criterio
  que la tool `Edit` de Claude Code con `old_string` (debe ser único o falla).
- Cada patch (completo o parcial) archiva primero un snapshot en `document_versions`
  — sin endpoint de restore en v1, es red de seguridad contra sobrescritura
  accidental, no un sistema de versionado navegable.
- **Transaccionalidad obligatoria del snapshot**: leer el contenido actual, insertar
  el snapshot en `document_versions` y aplicar el `UPDATE` deben ocurrir en **una
  sola transacción con la fila bloqueada desde la lectura** (`SELECT ... FOR
  UPDATE`). Sin ese lock, el read-then-write permitiría que dos peticiones casi
  simultáneas lean el mismo contenido previo y el historial quede con un snapshot
  duplicado en vez de la cadena real de versiones.

**Concurrencia — resuelta por el mecanismo de versionado ya definido, sin bloqueo
adicional**: no se espera concurrencia real (proyecto personal), pero si dos
peticiones llegaran casi al mismo tiempo, Postgres ya serializa los `UPDATE` sobre la
misma fila (la segunda espera a que la primera confirme). Como cada actualización
archiva el contenido anterior en `document_versions` antes de aplicarse, el resultado
es exactamente lo pedido: se procesan en orden de llegada, la más reciente queda como
estado actual, y ninguna de las dos se pierde — ambas quedan en el historial. No hace
falta optimistic locking ni ninguna mecánica nueva; basta con cumplir la
transaccionalidad del snapshot descrita arriba (snapshot + update en una transacción
con `SELECT ... FOR UPDATE`) — la serialización de Postgres solo garantiza el orden
si la lectura del contenido previo ocurre ya con el lock tomado.

## 13. Tokens transversales

Ni un token 100% compartido (exigiría una tabla de auth centralizada o que un
servicio dependa del otro en runtime — rompe la independencia de despliegue) ni
tokens completamente separados por servicio (duplica la gestión: crear y revocar dos
veces por agente). Solución adoptada:

- Cada servicio conserva su propia tabla `api_tokens` (su hash, sus scopes, su
  `allowed_contexts`/`allowed_categories` — esta última ahora referencia la tabla
  `categories` de §6) — sin esquema compartido, sin dependencia en tiempo real entre
  servicios.
- `cerebro token create <nombre> --scopes read,write` genera **un solo secreto** y lo
  registra (su hash) de forma independiente en las dos APIs en la misma operación —
  el agente usa un único `CEREBRO_TOKEN`, validado por separado por cada backend.
- `cerebro token revoke <nombre>` revoca en ambas a la vez.
- **Fallo parcial definido**: si el registro (o la revocación) tiene éxito en una API
  y falla en la otra, el CLI lo reporta explícitamente por servicio (dónde quedó
  registrado y dónde no) y el comando termina con error; el remedio es re-ejecutar el
  mismo comando (el registro es idempotente por nombre: re-registrar un token
  existente con el mismo hash no duplica filas) o revocar en la que sí quedó. Nunca
  se deja el estado a medias en silencio.
- Permite (opcional, no caso por defecto) permisos asimétricos por servicio si algún
  día hace falta, sin complicar el caso común.

## 14. Integración entre módulos: acoplamiento flojo

"Integrados" = viven juntos (repo, compose, cliente), no que se compartan datos:

- Nunca acceso cruzado a esquemas de Postgres entre cerebro-memory y cerebro-docs.
- Si algún día una memoria necesita referenciar un documento completo (ej. "la
  decisión X está detallada en el documento Y"), se hace vía **referencia URI en el
  contenido de la memoria** (`cerebro-docs://categoria/slug`), nunca por FK/join
  directo — mismo patrón que ya usa `security.py` con `secret://entorno/nombre`.
- Código compartido limitado a un solo caso legítimo: `cerebro-clients` (SDK de los
  dos clientes HTTP), consumido por `cerebro-cli` y `cerebro-mcp` — ambos son
  transportes distintos sobre las *mismas* llamadas, a diferencia de memory/docs que
  son dominios distintos. Todo lo demás (parseo de headings, auth) se duplica a
  propósito en vez de compartirse, para que cada módulo siga siendo independiente.
- Cohesión por convención (mismo vocabulario de scopes, mismo criterio de "nunca
  editar in-place", mismos prefijos de tools), no por acoplamiento de código.

## 15. Calidad y seguridad: etapa de auditoría obligatoria

Pedido explícito: "desarrollo sólido", con validación de calidad de código y de
seguridad como etapa formal, no opcional. Estrategia de pruebas (a mi criterio, según
lo pedido) y el gate de auditoría:

**Pruebas** — mismo patrón que ya usa cerebro-memory (`tests/`):
- Unitarias sin base de datos: generación/colisión de slugs, parseo de headings para
  `docs_patch_section`, vocabulario de operaciones de sección.
- Integración con Postgres real: CRUD de `categories`/`documents`, cascada de
  `DELETE /categories/{slug}`, unicidad `(category_id, slug)`, versionado en updates
  concurrentes — se saltan automáticamente si `DATABASE_URL` no está disponible, igual
  que hoy.
- `cerebro-clients`/`cerebro-mcp`/`cerebro-cli`: pruebas de que enrutan a la API
  correcta y no filtran lógica de negocio propia (deben seguir siendo adaptadores
  delgados).

**Auditoría antes de considerar cualquier paquete "terminado"** (gate obligatorio, no
un paso opcional al final):
1. **Revisión de seguridad**: `q` en `docs_search`/`GET /documents` debe ir
   parametrizado (riesgo de SQL injection en cualquier filtro de texto libre);
   verificar que la ausencia de filtro de credenciales (§2) esté documentada y no sea
   un descuido; confirmar que `allowed_categories` en tokens se aplica igual que
   `allowed_contexts` hoy; confirmar que el archivo de dump generado por `cerebro
   backup` (§9) no queda con permisos de archivo laxos.
2. **Revisión de calidad de código**: legibilidad, consistencia con el estilo ya
   establecido en cerebro-memory, SOLID/KISS — mismo criterio que ya se usa para
   revisar el resto del ecosistema.

Ninguna de las dos revisiones se hizo en esta sesión (no hay código todavía); quedan
como paso obligatorio en la ejecución, no como sugerencia.

## 16. Tabla resumen de decisiones y su porqué

| Decisión | Alternativa descartada | Por qué |
|---|---|---|
| Monorepo | Polyrepo + repo umbrella con `include:` | Compartir compose sin trucos; el costo de monorepo no aplica sin terceros consumiendo los repos |
| cerebro-docs separado de cerebro-memory (dominio) | Feature dentro de cerebro-memory | Rompe "memory over conversation"; distinto modelo de retrieval/versionado |
| Documentos NO se truncan/destilan | Tratarlos como memorias | El punto es guardar el documento íntegro |
| Solo Markdown, sin binarios/adjuntos | Soporte de otros formatos | Alcance explícito: cerebro-docs es "un repo de markdowns", nada más |
| Sin filtro de contenido/credenciales | Replicar `security.py` de memory | Decisión explícita: guardar lo que sea, dentro del límite de formato markdown |
| Categorías = tabla formal (`categories`, FK), como `contexts` | Texto libre autocreado (decisión previa, revisada) | Se necesita creación explícita y redistribución (rename) sin tocar cada documento |
| `category_id` FK en vez de texto copiado | Texto de categoría repetido por documento | Renombrar categoría es 1 UPDATE, no un UPDATE masivo con riesgo de colisión |
| Colisión de slug → error explícito, nunca auto-sufijo | Auto-sufijar (`-2`, `-3`) o sobrescribir | Mismo criterio conservador que headings duplicados y "nunca editar in-place sin pedirlo" |
| Categorías planas, sin subcategorías | Jerarquía de categorías | `contexts` tampoco la tiene; no hay necesidad medida, se puede simular con nombres |
| Paginación (`limit`/`offset`) en listados de docs | Sin paginación | Listar documentos completos es un caso de uso esperable, a diferencia de memoria |
| Backup = pg_dump simple, sin cambios (ya cubre ambos schemas) | Bundle con `.env`/config de entorno incluido | Lo pedido es reproducibilidad de datos, no del entorno completo; incluir `.env` sería alcance no solicitado |
| Versionado completo en `document_versions`, sin restore endpoint en v1 | Sobrescritura sin historial | Edición vía MCP en lenguaje natural tiene riesgo real de reescritura accidental |
| Concurrencia resuelta por versionado + serialización de Postgres | Bloqueo optimista adicional | Ya sale gratis del diseño existente; no se espera concurrencia real |
| Patch parcial por heading, falla en ambigüedad/ausencia | Adivinar o crear silenciosamente | Mismo criterio que `Edit` de Claude Code: unicidad obligatoria o error explícito |
| Postgres compartido, schemas separados | Un Postgres por módulo | Earn your complexity: aislamiento lógico sin duplicar infraestructura |
| MCP único (`cerebro-mcp`) | Un MCP por módulo | Es adaptador delgado sin lógica propia; unificar no acopla los backends, solo la presentación |
| CLI único (`cerebro-cli`) | Un CLI por módulo | Mismo razonamiento que el MCP |
| SDK compartido `cerebro-clients` | Duplicar llamadas HTTP en CLI y MCP | Único caso de código compartido legítimo: mismas llamadas, dos transportes, no dos dominios |
| Tokens transversales (mismo secreto, registro independiente por servicio) | Token 100% compartido o 100% separado | Evita servicio de auth centralizado y evita duplicar gestión de tokens por agente |
| Auditoría de seguridad y calidad como gate obligatorio | Revisión opcional/informal | Pedido explícito: "desarrollo sólido" |
| Rename `knowledgeos` → `cerebro-memory` | Mantener nombre interno | Prolijidad del ecosistema; lo ejecuta Jose Luis, no esta sesión |
| Referencias memoria↔documento vía URI (`cerebro-docs://...`) | FK/join directo entre esquemas | Mantiene acoplamiento flojo incluso si algún día hace falta puentear ambos módulos |

## 17. Preguntas abiertas / fuera de alcance de esta definición

- Estructura de carpetas exacta dentro de `packages/cerebro-docs/` (mirror de
  cerebro-memory) — no bocetada línea por línea, se sigue el mismo patrón conocido.
- Mecánica concreta de la migración de datos existentes al schema `cerebro_memory`
  (§5) — señalada como riesgo, no resuelta paso a paso.
- Si `docs_search` necesita evolucionar más allá de full-text simple (ej. `pg_trgm`
  para tolerancia a typos en títulos) — no hay necesidad medida todavía.
- Nombre/registro final de los paquetes en PyPI (si aplica) o si quedan solo como
  paquetes internos del monorepo sin publicar.

## 18. Backlog de mejoras futuras (no bloqueante para v1)

- **Presentación del CLI**: la salida actual de `knowledgeos stats` (futuro `cerebro
  memory stats`) no convence a Jose Luis en cuanto a formato/legibilidad. No se
  diseñó una solución en esta sesión — queda como mejora a revisar cuando se
  implemente `cerebro-cli` (§11), probablemente aplicando a la salida de todos los
  subcomandos (`memory stats`, `docs stats`), no solo al actual.
- **Importador bulk de markdown hacia cerebro-docs**: análogo a `import-markdown` de
  cerebro-memory pero SIN destilar (los documentos se guardan íntegros), para cargar
  de una vez muchos archivos existentes. No bloquea v1: `docs_save` cubre el caso
  documento a documento.
- **Harness de evaluación para `docs_search`**: sin él, cualquier mejora futura del
  retrieval de docs (ej. `pg_trgm` para typos, §17) se decidiría sin datos — contra
  el criterio "earn your complexity" de medir antes de complejizar que rige el resto
  del ecosistema. Análogo al `evals/` que ya tiene cerebro-memory.

## 19. Próximos pasos sugeridos para retomar la ejecución

0. **pg_dump completo del Postgres actual ANTES de cualquier migración de datos** —
   seguro obligatorio ante fallos a mitad de la migración del paso 2; se guarda
   fuera del árbol del repo.
1. Rename `knowledgeos` → `cerebro-memory` y restructuración a `packages/`
   (ejecutado por Claude como orquestador desde 2026-08-12, por decisión de Jose
   Luis — cambia lo dicho en la definición original).
2. Migrar las tablas existentes de cerebro-memory al schema `cerebro_memory` sin
   perder datos (§5).
3. Bocetar `packages/cerebro-docs/` en espejo de `cerebro-memory` (migraciones SQL
   para `categories`/`documents`/`document_versions`, `api.py`, tests de parseo de
   headings y de colisión de slugs).
4. Implementar `packages/cerebro-clients/` (MemoryClient, DocsClient) antes de
   `cerebro-mcp`/`cerebro-cli`, ya que ambos dependen de él.
5. Implementar `cerebro-mcp` unificado y `cerebro-cli` unificado, incluyendo
   `cerebro token create/revoke` transversal (§13).
6. Confirmar que `cerebro backup`/`restore` apunta a la instancia completa de
   Postgres (ambos schemas) — sin cambios de código más allá de eso (§9).
7. Escribir el `compose.yaml` único con los dos schemas y ambos servicios API.
8. **Gate obligatorio antes de cerrar cada paquete**: revisión de seguridad + revisión
   de calidad de código (§15) — no se salta ni se deja para "después".
9. Dogfooding: guardar este mismo documento (`ecosistema-cerebro.md`) como el primer
   documento real en cerebro-docs, categoría `ecosistema`.
