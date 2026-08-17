---
name: cerebro-memoria
description: Protocolo obligatorio de memoria y documentación persistente usando el MCP "cerebro". Actívala SIEMPRE al inicio de una conversación con el usuario, y cada vez que la respuesta pueda depender de quién es el usuario, sus preferencias, sus proyectos, decisiones pasadas, documentos guardados o cualquier cosa contada en sesiones anteriores — aunque el usuario no mencione la palabra "memoria" o "documento". Actívala también cuando el usuario comparta un hecho, preferencia, decisión o evento que valga la pena recordar, cuando quiera guardar/leer/editar un documento completo, cuando pida recordar/olvidar/actualizar algo, o cuando pregunte por lo que ya sabes de él o de su trabajo. Si estás a punto de afirmar que desconoces al usuario, su contexto, su historial o un documento suyo, esta skill aplica y te obliga a buscar primero.
---

# Cerebro: memoria y documentación persistente

"Cerebro" es la memoria y el repositorio de documentos a largo plazo del
usuario. Las conversaciones se pierden entre sesiones; cerebro no. Por eso
la fuente de verdad sobre el usuario, sus proyectos, sus decisiones pasadas
y sus documentos es cerebro, no tu contexto de conversación ni tus
suposiciones.

Cerebro tiene dos módulos con propósitos distintos y complementarios:

- **cerebro-memory** (`memory_*`) — memoria destilada: hechos, preferencias,
  decisiones y eventos reducidos a 1–3 frases ("memory over conversation").
  Optimizada para recuperarse rápido y guiar el razonamiento.
- **cerebro-docs** (`docs_*`) — documentos Markdown **completos**, sin
  truncar: guías, especificaciones, actas, README, cualquier texto que deba
  conservarse íntegro. No cuentan como memoria formal ni se destilan.

Los dos módulos están débilmente acoplados: una memoria puede referenciar un
documento completo con una URI en su `content` (ej.
`cerebro-docs://categoria/slug`), pero nunca hay acceso cruzado directo
entre ambos almacenes.

## Regla cero: recordar antes de asumir

**Está prohibido afirmar que desconoces al usuario, su contexto, su
historial o un documento suyo sin haber buscado antes** con `memory_search`
(y `docs_search`/`docs_list` cuando la pregunta apunta a un documento
completo). Cualquier declaración de desconocimiento — o pedirle al usuario
información que cerebro podría tener — solo es válida DESPUÉS de una
búsqueda que volvió vacía, y en ese caso se presenta como resultado: "busqué
en tu memoria/documentos y no encontré nada sobre X".

La regla simétrica también aplica: **está prohibido inventar recuerdos o
documentos.** Nunca atribuyas al usuario un hecho, preferencia, decisión o
documento "de una sesión anterior" que no venga de un resultado real de
`memory_search`, `memory_timeline`, `memory_related`, `docs_search`,
`docs_get` o `docs_list`. Si no está en cerebro, no lo "recuerdas".

## Protocolo de inicio de conversación

1. Si la petición puede depender de contexto previo (quién es el usuario, en
   qué trabaja, qué prefiere, qué se decidió antes), llama `memory_search`
   con una consulta en lenguaje natural ANTES de responder. No preguntes al
   usuario algo que la memoria probablemente ya sabe.
2. Si la petición apunta a un documento completo (una guía, un acta, un
   README, algo que el usuario "guardó" o "escribió" antes), usa
   `docs_search` (texto impreciso) o `docs_get` (ruta exacta
   `/{categoria}/{slug}` si ya la conocés).
3. Si es la primera vez que trabajas con este usuario, o no sabes cómo
   organiza su conocimiento, llama `memory_contexts` (contextos de memoria)
   y/o `docs_categories` (categorías de documentos) antes de guardar nada
   nuevo.
4. Solo si las búsquedas relevantes vuelven vacías, trata al usuario o al
   tema como nuevos — y dilo como resultado de la búsqueda, no como
   suposición.

No hace falta buscar para peticiones autocontenidas que no dependen de nadie
en particular ("¿cuánto es 2+2?", "explícame qué es un mutex"). Ante la
duda, busca: una búsqueda de más es barata; asumir mal, no.

## ¿Memoria o documento?

| Si el usuario... | Usa |
|---|---|
| Cuenta un hecho, preferencia, decisión o evento en 1-3 frases | `memory_remember` |
| Pide guardar/leer/editar un texto largo completo (guía, spec, acta, README) | `docs_save` / `docs_get` / `docs_update` |
| Pregunta "¿qué sabés de mí / de este proyecto?" | `memory_search` (y `docs_search` si podría haber un documento relevante) |
| Pregunta "¿dónde quedó guardada la guía/documento de X?" | `docs_search` o `docs_list` |
| Quiere que un hecho apunte a un documento sin duplicar su contenido | `memory_remember` con `cerebro-docs://categoria/slug` en el `content` |

## Herramientas de memoria (`memory_*`)

| Herramienta | Úsala para |
|---|---|
| `memory_search` | Recuperar hechos/decisiones/eventos antes de responder. Primera llamada por defecto. |
| `memory_contexts` | Ver los contextos existentes y sus descripciones. |
| `memory_create_context` | Crear un contexto nuevo SOLO si ninguno existente encaja. |
| `memory_remember` | Guardar algo nuevo que valga a largo plazo. |
| `memory_update` | Un hecho ya guardado cambió (nueva versión, conserva historial). |
| `memory_forget` | El usuario pide olvidar algo, o una memoria quedó obsoleta sin reemplazo. |
| `memory_link` | Conectar dos memorias existentes (decisión→causa, procedimiento→proyecto…). |
| `memory_related` | Ver los vecinos a 1 salto de una memoria (relaciones + versiones). |
| `memory_timeline` | "¿Qué pasó en X últimamente?" — eventos y decisiones por fecha. |
| `memory_stats` | El usuario quiere ver el estado/aprendizaje del sistema. |

## Herramientas de documentos (`docs_*`)

| Herramienta | Úsala para |
|---|---|
| `docs_search` | Buscar documentos por texto (título + contenido) ante una referencia imprecisa ("la guía de despliegue"). |
| `docs_get` | Leer un documento completo cuando ya sabés su ruta exacta `/{categoria}/{slug}`. |
| `docs_list` | Explorar qué documentos existen, en una categoría o en todo el repositorio. |
| `docs_categories` | Ver las categorías existentes y sus descripciones. Llamarla antes de `docs_save` si no sabés cuál usar. |
| `docs_create_category` | Crear una categoría nueva SOLO si ninguna existente encaja. |
| `docs_save` | Guardar un documento Markdown **nuevo**, completo, sin destilar. Falla si el slug ya existe en esa categoría — nunca sobrescribe en silencio. |
| `docs_update` | Reemplazo COMPLETO de un documento existente (puede moverlo de categoría). Archiva un snapshot del contenido anterior antes de reemplazar. |
| `docs_patch_section` | Parche PARCIAL por sección (heading → siguiente heading de igual o mayor nivel). Operaciones: `replace`, `append`, `insert_after`, `insert_before`, `delete`. El `heading` debe matchear exacto y ser único, o falla. |
| `docs_delete` | Borra un documento y todo su historial de versiones. **Irreversible** (a diferencia de `memory_forget`) — confirmá con el usuario si hay dudas. |

## Buscar: `memory_search`

- Escribe la consulta en lenguaje natural, como la pregunta que intentas
  responder.
- **Omite `context` por defecto**: el Context Engine decide solo (scope
  automático). Pásalo solo si ya sabes con certeza de qué contexto se trata.
- **Si la respuesta trae `ambiguous: true`**: `results` viene vacío a
  propósito. El campo `message` lista los contextos candidatos con evidencia.
  Decide tú con el contexto de la conversación cuál corresponde (o pregunta
  al usuario si de verdad no es deducible) y **repite la llamada con
  `context=<slug>`**. Esa segunda llamada, además de buscar, le enseña al
  servidor a resolver solo consultas parecidas en el futuro — por eso importa
  resolverla en la llamada inmediatamente siguiente y no dejarla colgada.
- Usa `type` para filtrar ("decision" para "¿por qué decidimos X?") y
  `expand=True` cuando quieras traer también las memorias relacionadas a los
  resultados. Ojo: `related` son vecinos por relación, no resultados de la
  búsqueda — no los presentes con la misma confianza.
- **Búsqueda vacía ≠ licencia para inventar.** Di que no hay nada guardado
  sobre eso y, si aplica, ofrece guardarlo.

## Guardar: `memory_remember`

Guarda cuando el usuario comparta algo con valor futuro:

- **`semantic`** — hechos y preferencias estables: "uso Next.js en el
  proyecto X", "prefiero respuestas en español".
- **`episodic`** — eventos puntuales con fecha: "hoy se cayó el servidor de
  producción".
- **`procedural`** — cómo se hace algo: "el deploy se hace con `make deploy`
  desde main".
- **`decision`** — una decisión y su motivo: "elegimos Postgres porque Mongo
  subió precios". Guarda siempre el porqué, no solo el qué.

Reglas de escritura:

- `context` y `type` son **obligatorios**. Si no sabes el contexto, llama
  `memory_contexts` primero y elige el que mejor encaje por su descripción.
  Crea uno nuevo con `memory_create_context` solo si de verdad ninguno
  aplica — los contextos existen para AISLAR información que no debe
  mezclarse (las finanzas personales del usuario no son el proyecto de
  finanzas de un cliente), no para coleccionar carpetas redundantes.
- Redacta `content` en 1–3 frases autocontenidas: alguien sin esta
  conversación debe entenderlas. Convierte fechas relativas ("ayer", "la
  semana pasada") en absolutas.
- Antes de guardar algo que suena a ya-conocido, busca primero: si existe una
  memoria equivalente y el hecho cambió, es `memory_update`, no un duplicado.
- **No guardes**: detalles efímeros de la conversación actual, cosas que el
  repo/código ya registra, ni secretos (contraseñas, API keys, tokens — la
  API los rechaza; guarda una referencia `secret://entorno/nombre` si hace
  falta).
- Si guardaste algo, díselo al usuario en una línea ("guardado en
  `<contexto>`"). La memoria es del usuario, no tuya: nada de escrituras
  silenciosas.

## Guardar y editar documentos: `docs_save` / `docs_update` / `docs_patch_section`

- `docs_save` guarda el documento **íntegro**, tal cual — no lo resumas ni
  lo recortes. `category` es obligatoria y debe existir: revisá con
  `docs_categories()` y creá una nueva con `docs_create_category` solo si
  ninguna encaja.
- Si el `slug` ya existe en esa categoría, `docs_save` falla en vez de
  sobrescribir. Para editar un documento existente usá `docs_update`
  (reemplazo completo, con snapshot automático del contenido anterior) o
  `docs_patch_section` (edición quirúrgica de una sola sección por
  `heading`).
- Antes de guardar un documento que suena a ya-existente, buscá primero con
  `docs_search` o `docs_get` — igual que con memoria, evitá duplicados
  compitiendo entre sí.
- `docs_delete` es irreversible (borra también el historial de versiones).
  Confirmá con el usuario antes de llamarla si hay cualquier duda.
- Si guardaste, actualizaste o borraste un documento, decíselo al usuario en
  una línea con su ruta (`/<categoria>/<slug>`).

## Actualizar, enlazar u olvidar: elige bien

- **El hecho cambió** (nueva tarifa, nueva versión, cambió el plan) →
  `memory_update(memory_id, content)`. Crea la versión nueva y marca la vieja
  como reemplazada, conservando historial. NUNCA crees una memoria suelta
  que compita con la vieja en búsquedas.
- **Dos memorias vigentes se relacionan** → `memory_link` con una de sus 5
  relaciones: `caused_by` (decisión→su causa), `part_of`
  (procedimiento→su proyecto), `follows` (episodio→su consecuencia),
  `contradicts` (conflicto real sin supersedencia clara), `relates_to`
  (asociación genérica, último recurso). Patrón de oro: al guardar una
  `decision`, enlázala a la memoria de su causa si existe.
- **Ya no debe aparecer** → `memory_forget(memory_id)`: archiva
  (recuperable). `hard=True` borra de forma irreversible — úsalo SOLO si el
  usuario lo pide explícitamente (p.ej. se guardó algo sensible por error) y
  confirma antes con él.
- Documentos: `docs_update` conserva historial (recuperable vía versiones);
  `docs_delete` NO tiene papelera ni soft-delete — es la operación más
  destructiva de todo cerebro, tratala con la misma cautela que un `rm -rf`.

## Errores y honestidad

- Si una herramienta devuelve `error` (API caída, token inválido, contexto o
  categoría inexistente), repórtalo al usuario tal cual y con la acción
  sugerida del mensaje. **Un error de conexión no te autoriza a responder
  "de memoria"**: di que cerebro no está disponible ahora mismo.
- Distingue siempre las tres situaciones en tu respuesta: (a) lo encontré en
  cerebro (memoria o documento), (b) busqué y no hay nada guardado, (c) no
  pude consultar cerebro. Nunca presentes (b) o (c) como si fuera
  conocimiento.

## Resumen operativo

1. ¿Puede depender del pasado? → `memory_search` primero. ¿Podría haber un
   documento completo relevante? → sumá `docs_search`/`docs_get`. Sin
   excepciones para preguntas sobre el usuario, su historial o sus
   documentos.
2. ¿Ambigua la memoria? → resolver con `context=<slug>` en la llamada
   siguiente.
3. ¿Algo nuevo con valor futuro? Si es un hecho/decisión corto →
   `memory_remember` con contexto y tipo correctos. Si es un documento
   completo → `docs_save` con categoría correcta. Avisa al usuario en ambos
   casos.
4. ¿Cambió un hecho? → `memory_update`. ¿Cambió un documento entero? →
   `docs_update`. ¿Solo una sección? → `docs_patch_section`. ¿Se relacionan
   dos memorias? → `memory_link`. ¿Sobró una memoria? → `memory_forget`
   (soft por defecto). ¿Sobró un documento? → `docs_delete`, con
   confirmación previa por ser irreversible.
5. Nunca inventes recuerdos ni documentos; nunca declares ignorancia sin
   haber buscado.
