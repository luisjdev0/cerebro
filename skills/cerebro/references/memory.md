# cerebro-memory (`memory_*`)

Memoria destilada: hechos, preferencias, decisiones y eventos reducidos a 1–3
frases autocontenidas. No es un archivo de conversaciones — es un resumen
curado, pensado para recuperarse rápido y guiar el razonamiento, no para
reconstruir la charla completa.

## Herramientas

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

## Buscar: `memory_search`

- Escribí la consulta en lenguaje natural, como la pregunta que intentás
  responder.
- **Omití `context` por defecto**: el Context Engine decide solo (scope
  automático). Pasalo solo si ya sabés con certeza de qué contexto se trata.
- **Si la respuesta trae `ambiguous: true`**: `results` viene vacío a
  propósito. El campo `message` lista los contextos candidatos con evidencia.
  Decidí vos (con el contexto de la conversación) cuál corresponde, o
  preguntale al usuario si de verdad no es deducible, y **repetí la llamada
  con `context=<slug>`**. Esa segunda llamada, además de buscar, le enseña al
  servidor a resolver solo consultas parecidas en el futuro — por eso importa
  resolverla en la llamada inmediatamente siguiente y no dejarla colgada.
- Usá `type` para filtrar ("decision" para "¿por qué decidimos X?") y
  `expand=True` cuando quieras traer también las memorias relacionadas a los
  resultados. Ojo: `related` son vecinos por relación, no resultados de la
  búsqueda — no los presentes con la misma confianza.
- **Búsqueda vacía ≠ licencia para inventar.** Decí que no hay nada guardado
  sobre eso y, si aplica, ofrecé guardarlo.

## Guardar: `memory_remember`

Guardá cuando el usuario comparta algo con valor futuro:

- **`semantic`** — hechos y preferencias estables: "uso Next.js en el
  proyecto X", "prefiero respuestas en español".
- **`episodic`** — eventos puntuales con fecha: "hoy se cayó el servidor de
  producción".
- **`procedural`** — cómo se hace algo: "el deploy se hace con `make deploy`
  desde main".
- **`decision`** — una decisión y su motivo: "elegimos Postgres porque Mongo
  subió precios". Guardá siempre el porqué, no solo el qué.

Reglas de escritura:

- `context` y `type` son **obligatorios**. Si no sabés el contexto, llamá
  `memory_contexts` primero y elegí el que mejor encaje por su descripción.
  Creá uno nuevo con `memory_create_context` solo si de verdad ninguno
  aplica — los contextos existen para AISLAR información que no debe
  mezclarse (las finanzas personales del usuario no son el proyecto de
  finanzas de un cliente), no para coleccionar carpetas redundantes.
- Redactá `content` en 1–3 frases autocontenidas: alguien sin esta
  conversación debe entenderlas. Convertí fechas relativas ("ayer", "la
  semana pasada") en absolutas.
- Antes de guardar algo que suena a ya-conocido, buscá primero: si existe una
  memoria equivalente y el hecho cambió, es `memory_update`, no un
  duplicado.
- **No guardes**: detalles efímeros de la conversación actual, cosas que el
  repo/código ya registra, ni secretos (contraseñas, API keys, tokens — la
  API los rechaza; guardá una referencia `secret://entorno/nombre` si hace
  falta).
- Si guardaste algo, decíselo al usuario en una línea ("guardado en
  `<contexto>`"). La memoria es del usuario, no tuya: nada de escrituras
  silenciosas.

## Actualizar, enlazar u olvidar: elegí bien

- **El hecho cambió** (nueva tarifa, nueva versión, cambió el plan) →
  `memory_update(memory_id, content)`. Crea la versión nueva y marca la vieja
  como reemplazada, conservando historial. NUNCA crees una memoria suelta
  que compita con la vieja en búsquedas.
- **Dos memorias vigentes se relacionan** → `memory_link` con una de sus 5
  relaciones: `caused_by` (decisión→su causa), `part_of`
  (procedimiento→su proyecto), `follows` (episodio→su consecuencia),
  `contradicts` (conflicto real sin supersedencia clara), `relates_to`
  (asociación genérica, último recurso). Patrón de oro: al guardar una
  `decision`, enlazala a la memoria de su causa si existe.
- **Ya no debe aparecer** → `memory_forget(memory_id)`: archiva
  (recuperable). `hard=True` borra de forma irreversible — usalo SOLO si el
  usuario lo pide explícitamente (p.ej. se guardó algo sensible por error) y
  confirmá antes con él.
