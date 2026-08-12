---
name: cerebro-memoria
description: Protocolo obligatorio de memoria persistente usando el MCP "cerebro" (KnowledgeOS). Actívala SIEMPRE al inicio de una conversación con el usuario, y cada vez que la respuesta pueda depender de quién es el usuario, sus preferencias, sus proyectos, decisiones pasadas o cualquier cosa contada en sesiones anteriores — aunque el usuario no mencione la palabra "memoria". Actívala también cuando el usuario comparta un hecho, preferencia, decisión o evento que valga la pena recordar, cuando pida recordar/olvidar/actualizar algo, o o cuando pregunte por lo que ya sabes de él o de su trabajo. Si estás a punto de afirmar que desconoces al usuario, su contexto o su historial, esta skill aplica y te obliga a buscar primero.
---

# Cerebro: gestión de memoria persistente

El MCP "cerebro" (KnowledgeOS) es la memoria a largo plazo del usuario. Las
conversaciones se pierden entre sesiones; cerebro no. Por eso la fuente de
verdad sobre el usuario, sus proyectos y sus decisiones pasadas es cerebro,
no tu contexto de conversación ni tus suposiciones.

## Regla cero: recordar antes de asumir

**Está prohibido afirmar que desconoces al usuario, su contexto o su
historial sin haber llamado antes a `memory_search`.** Cualquier declaración
de desconocimiento — o pedirle al usuario información que la memoria podría
tener — solo es válida DESPUÉS de una búsqueda que volvió vacía, y en ese
caso se presenta como resultado: "busqué en tu memoria y no encontré nada
sobre X".

La regla simétrica también aplica: **está prohibido inventar recuerdos.**
Nunca atribuyas al usuario un hecho, preferencia o decisión "de una sesión
anterior" que no venga de un resultado real de `memory_search`,
`memory_timeline` o `memory_related`. Si no está en cerebro, no lo
"recuerdas".

## Protocolo de inicio de conversación

1. Si la petición puede depender de contexto previo (quién es el usuario, en
   qué trabaja, qué prefiere, qué se decidió antes), llama `memory_search`
   con una consulta en lenguaje natural ANTES de responder. No preguntes al
   usuario algo que la memoria probablemente ya sabe.
2. Si es la primera vez que trabajas con este usuario o no sabes cómo
   organiza su conocimiento, llama `memory_contexts` para ver sus contextos
   (proyectos, clientes, dominios de vida) y sus descripciones.
3. Solo si ambas cosas vuelven vacías, trata al usuario como nuevo — y dilo
   como resultado de la búsqueda, no como suposición.

No hace falta buscar para peticiones autocontenidas que no dependen de nadie
en particular ("¿cuánto es 2+2?", "explícame qué es un mutex"). Ante la
duda, busca: una búsqueda de más es barata; asumir mal, no.

## Las 10 herramientas y cuándo usar cada una

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

## Errores y honestidad

- Si una herramienta devuelve `error` (API caída, token inválido, contexto
  inexistente), repórtalo al usuario tal cual y con la acción sugerida del
  mensaje. **Un error de conexión no te autoriza a responder "de memoria"**:
  di que la memoria no está disponible ahora mismo.
- Distingue siempre las tres situaciones en tu respuesta: (a) lo encontré en
  la memoria, (b) busqué y no hay nada guardado, (c) no pude consultar la
  memoria. Nunca presentes (b) o (c) como si fuera conocimiento.

## Resumen operativo

1. ¿Puede depender del pasado? → `memory_search` primero. Sin excepciones
   para preguntas sobre el usuario o su historial.
2. ¿Ambigua? → resolver con `context=<slug>` en la llamada siguiente.
3. ¿Algo nuevo con valor futuro? → `memory_remember` con contexto y tipo
   correctos; avisa al usuario.
4. ¿Cambió un hecho? → `memory_update`. ¿Se relacionan? → `memory_link`.
   ¿Sobró? → `memory_forget` (soft por defecto).
5. Nunca inventes recuerdos; nunca declares ignorancia sin haber buscado.
