---
name: cerebro
description: Protocolo obligatorio para usar el ecosistema "cerebro" (MCP con memoria persistente, documentos completos y flujos de trabajo). Actívala SIEMPRE al inicio de una conversación con el usuario, y cada vez que la respuesta pueda depender de quién es el usuario, sus preferencias, sus proyectos, decisiones pasadas, documentos guardados, o un proceso/checklist con pasos que deba ejecutarse o crearse — aunque el usuario no diga las palabras "memoria", "documento" o "flujo". Actívala también cuando el usuario comparta un hecho, preferencia, decisión o evento que valga la pena recordar; cuando quiera guardar/leer/editar/archivar un documento; cuando quiera ejecutar, retomar o crear un proceso paso a paso (onboarding, incidentes, checklists con aprobaciones); cuando pida recordar/olvidar/actualizar algo; o cuando pregunte qué sabes de él o de su trabajo. Si estás a punto de afirmar que desconoces al usuario, su contexto, un documento suyo, o cómo seguir un proceso en curso, esta skill aplica y te obliga a consultar cerebro primero, nunca a suponer.
---

# Cerebro: memoria, documentos y flujos persistentes

"Cerebro" es la memoria, el repositorio de documentos y el motor de procesos a
largo plazo del usuario. Las conversaciones se pierden entre sesiones; cerebro
no. Por eso la fuente de verdad sobre el usuario, sus proyectos, sus
decisiones pasadas, sus documentos y sus procesos en curso es cerebro, nunca
tu contexto de conversación ni tus suposiciones.

Cerebro tiene tres módulos con propósitos distintos y complementarios,
débilmente acoplados entre sí (un módulo puede referenciar a otro por URI o
código, pero nunca hay acceso cruzado directo a sus datos):

- **cerebro-memory** (`memory_*`) — memoria destilada: hechos, preferencias,
  decisiones y eventos reducidos a 1–3 frases ("memory over conversation").
  Optimizada para recuperarse rápido y guiar el razonamiento.
- **cerebro-docs** (`docs_*`) — documentos Markdown **completos**, sin
  truncar: guías, especificaciones, actas, runbooks, cualquier texto que deba
  conservarse íntegro. No cuentan como memoria formal ni se destilan.
- **cerebro-flows** (`flow_*`) — procesos con pasos, decisiones y checkpoints
  de aprobación, definidos en YAML. El servidor revela el proceso **paso a
  paso**, nunca la definición completa de una vez — así no es posible
  saltarse un checkpoint que todavía no se ha visto. Tú (el modelo) sigues
  ejecutando las acciones reales con tus propias tools; cerebro-flows solo
  decide qué paso toca ver y cuándo. También puedes **crear** flujos nuevos,
  no solo ejecutarlos.

## Regla cero: recordar antes de asumir

**Está prohibido afirmar que desconoces al usuario, su contexto, su
historial, un documento suyo, o el estado de un proceso en curso, sin haber
consultado cerebro antes.** Cualquier declaración de desconocimiento — o
pedirle al usuario información que cerebro podría tener — solo es válida
DESPUÉS de una consulta que volvió vacía, y en ese caso se presenta como
resultado: "busqué en tu memoria/documentos y no encontré nada sobre X".

La regla simétrica también aplica: **está prohibido inventar recuerdos,
documentos o pasos de un proceso.** Nunca atribuyas al usuario un hecho,
documento o decisión "de una sesión anterior" que no venga de un resultado
real de una tool de cerebro, y nunca inventes qué paso sigue en un flujo —
eso solo lo dice `flow_next`. Si no está en cerebro, no lo "recordás" ni lo
"suponés".

## Protocolo de inicio de conversación

1. Si la petición puede depender de contexto previo (quién es el usuario, en
   qué trabaja, qué prefiere, qué se decidió antes), llamá `memory_search`
   con una consulta en lenguaje natural ANTES de responder.
2. Si la petición apunta a un documento completo (una guía, un acta, un
   README, algo que el usuario "guardó" o "escribió" antes), usá
   `docs_search` (texto impreciso) o `docs_get` (ruta exacta si ya la
   conocés).
3. Si la petición apunta a un proceso con pasos (un onboarding, un
   checklist, un incidente que ya se venía trabajando), buscá si ya existe
   una definición (`flow_list`/`flow_get`) o un `run_id` en curso antes de
   improvisar los pasos vos mismo.
4. Si es la primera vez que trabajás con este usuario, o no sabés cómo
   organiza su conocimiento, llamá `memory_contexts`, `docs_categories` y/o
   `flow_categories` antes de guardar nada nuevo.
5. Solo si las consultas relevantes vuelven vacías, tratá al usuario o al
   tema como nuevos — y decilo como resultado de la consulta, no como
   suposición.

No hace falta consultar nada para peticiones autocontenidas que no dependen
de nadie en particular ("¿cuánto es 2+2?", "explicame qué es un mutex"). Ante
la duda, consultá: una consulta de más es barata; asumir mal, no.

## ¿Memoria, documento o flujo?

| Si el usuario... | Usá |
|---|---|
| Cuenta un hecho, preferencia, decisión o evento en 1-3 frases | `memory_remember` (ver `references/memory.md`) |
| Pide guardar/leer/editar un texto largo completo (guía, spec, acta, runbook) | `docs_save`/`docs_get`/`docs_update` (ver `references/docs.md`) |
| Quiere ejecutar o retomar un proceso con pasos/decisiones/aprobaciones | `flow_start`/`flow_next` (ver `references/flows.md`) |
| Quiere definir un proceso nuevo (checklist, runbook con pasos formales) para poder ejecutarlo después | `flow_validate` → `flow_save` (ver `references/flows.md`) |
| Pregunta "¿qué sabés de mí / de este proyecto?" | `memory_search` (y `docs_search` si podría haber un documento relevante) |
| Pregunta "¿dónde quedó guardada la guía/documento de X?" | `docs_search` o `docs_list` |
| Pregunta "¿en qué paso quedó el proceso de X?" | `flow_get`/estado del `run_id` (ver `references/flows.md`) |
| Quiere que un hecho apunte a un documento sin duplicar su contenido | `memory_remember` con `cerebro-docs://categoria/slug` en el `content` |

## Referencias por módulo

El detalle de cada módulo (tablas de tools, cuándo usar cada una, reglas de
escritura, ejemplos) vive en un archivo aparte — leelo cuando la tarea entre
en ese módulo, no hace falta cargarlos todos de una vez:

- **`references/memory.md`** — buscar/guardar/actualizar/enlazar/olvidar
  memorias (`memory_*`).
- **`references/docs.md`** — buscar/guardar/editar/archivar documentos,
  categorías ocultas, redirects de ruta (`docs_*`).
- **`references/flows.md`** — ejecutar un flujo paso a paso Y crear/editar
  definiciones nuevas, incluido el formato YAML (`flow_*`).

## Errores y honestidad

- Si una tool devuelve `error` (API caída, token inválido, contexto/categoría
  inexistente), reportalo al usuario tal cual y con la acción sugerida del
  mensaje. **Un error de conexión no te autoriza a responder "de memoria"**:
  decí que cerebro no está disponible ahora mismo.
- Si una respuesta trae `alert` (p.ej. `docs_get` avisando que una ruta se
  movió), tratalo como instrucción accionable, no como nota al pie: dejá de
  usar la ruta vieja de ahí en adelante y corregila en cualquier memoria o
  documento donde la tuvieras guardada.
- Distinguí siempre las tres situaciones en tu respuesta: (a) lo encontré en
  cerebro, (b) consulté y no hay nada guardado, (c) no pude consultar
  cerebro. Nunca presentes (b) o (c) como si fuera conocimiento.

## Resumen operativo

1. ¿Puede depender del pasado? → `memory_search` primero. ¿Podría haber un
   documento completo relevante? → sumá `docs_search`/`docs_get`. ¿Es o
   podría ser parte de un proceso con pasos? → sumá `flow_list`/`flow_get`.
   Sin excepciones para preguntas sobre el usuario, su historial, sus
   documentos o sus procesos.
2. ¿Ambigua la memoria? → resolvé con `context=<slug>` en la llamada
   siguiente (ver `references/memory.md`).
3. ¿Algo nuevo con valor futuro? Hecho/decisión corto → `memory_remember`.
   Documento completo → `docs_save`. Proceso nuevo con pasos → redactá el
   YAML, validalo con `flow_validate`, y guardalo con `flow_save`. Avisá al
   usuario en los tres casos.
4. ¿Cambió algo? → `memory_update` / `docs_update` (o `docs_patch_section`
   para solo una sección) / `flow_update`. ¿Se relacionan dos memorias? →
   `memory_link`. ¿Sobró algo? → `memory_forget`/`docs_archive` (reversibles,
   preferilos) o `docs_delete`/`flow_delete` (irreversibles, con
   confirmación previa).
5. ¿Hay un proceso en curso? → seguilo con `flow_next` paso a paso, nunca
   leyendo la definición completa para "adivinar" el siguiente paso.
6. Nunca inventes recuerdos, documentos ni pasos de un proceso; nunca
   declares ignorancia sin haber consultado cerebro primero.
