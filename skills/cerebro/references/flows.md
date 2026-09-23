# cerebro-flows (`flow_*`)

Un tercer tipo de contenido, además de memoria (hechos destilados) y
documentos (Markdown completo): procesos con pasos, decisiones y checkpoints
de aprobación, definidos en YAML. `cerebro-flows` es un **semáforo**, no un
orquestador: nunca ejecuta tools de terceros por su cuenta (Jira, SSH, lo que
sea) — vos seguís siendo quien ejecuta las acciones reales con tus propias
tools. Su único trabajo es decidir qué paso te toca ver y cuándo, y nunca
entregarte la definición completa de una vez.

Esta referencia cubre dos cosas separadas: **ejecutar** un flujo existente
(la mayoría de las veces) y **crear/editar** una definición nueva.

## Ejecutar un flujo (protocolo paso a paso)

**Nunca leas una definición completa (`flow_get`) para "seguirla" a mano.**
El servidor revela un paso a la vez a propósito — así "no deberías saltarte
un checkpoint" se vuelve "no podés ver el siguiente paso hasta que lo
aprobaste", un problema de diseño, no de disciplina tuya.

1. **`flow_start(code)`** → `{run_id, status, step}`. Guardá el `run_id`: lo
   necesitás en TODAS las llamadas siguientes (es un token opaco, no hay
   sesión implícita).
2. El primer `step` casi siempre es sintético
   (`id: "__prerequisites__"`, `tools_required: [...]`): confirmá que tenés
   esas tools disponibles (intentá `ToolSearch` si alguna está diferida en tu
   entorno) antes de seguir. Si de verdad falta alguna, avisá al usuario y NO
   continúes — no hay forma de "saltarse" este paso.
3. Llamá **`flow_next(run_id)`** en loop para avanzar. Si el `step` que te
   devuelve es `type: "decision"`, tu PRÓXIMA llamada a `flow_next` debe
   incluir `decision` con una de las claves de `branches` — el servidor no
   infiere la condición, vos la reportás explícitamente en base a lo que
   observaste/decidiste.
4. Si un `step` trae `checkpoint`, `flow_next` se BLOQUEA (409) hasta que
   llames **`flow_approve_checkpoint(run_id)`** o
   **`flow_reject_checkpoint(run_id, reason)`**:
   - Aprobar es la acción de mayor consecuencia de un flujo — si tenés dudas
     sobre si el usuario aprobaría este paso puntual, confirmá con él ANTES
     de llamar `flow_approve_checkpoint`, igual que harías con cualquier
     acción irreversible.
   - Rechazar no detiene el flujo: lo redirige al step de
     `checkpoint.on_reject` de la definición (p.ej. de vuelta a revisar un
     paso anterior).
5. `status: "completed"` (con `step: null`) marca el fin exitoso. No hay que
   reconocer ningún texto libre tipo "fin del flujo" — es un campo
   estructurado.
6. Si el usuario decide explícitamente no continuar con un proceso ya
   empezado, usá **`flow_abort(run_id, reason?)`** — es distinto de un
   checkpoint rechazado (que reencamina, no termina) y es irreversible.

Un `run_id` abandonado sin `flow_abort` expira solo por inactividad (el
servidor lo maneja con un TTL) — no necesitás "limpiarlo" vos, pero preferí
abortar explícitamente cuando sepas que el proceso no va a continuar, para
que quede reflejado en el historial.

## Crear o editar un flujo

Las tools de autoría son de primera clase — no hace falta pasar por el CLI
para definir un proceso nuevo:

1. **Elegí o creá una categoría** con `flow_categories()` / 
   `flow_create_category(slug, code, name, description?)`. El `code` de la
   categoría es el prefijo del id de sus flujos (categoría `incident`/`INC`
   → flujos `INC-1`, `INC-2`...).
2. **Escribí el YAML** (ver el schema completo más abajo).
3. **Validá antes de guardar** con `flow_validate(yaml_content)` — corre
   exactamente la misma validación que `flow_save`/`flow_update` harían,
   pero sin tocar la base de datos. Te devuelve el error puntual (qué step,
   qué campo) si algo está mal; iterá sobre eso antes de comprometer nada.
4. **Guardá** con `flow_save(category, yaml_content, code?)` — el `code` se
   autogenera si lo omitís. Para modificar un flujo existente usá
   `flow_update(code, yaml_content)` (reemplaza el YAML entero, versionado:
   una ejecución ya en curso sigue la versión con la que arrancó).
5. `flow_get(code)`/`flow_list(category?)` para inspeccionar lo que ya
   existe antes de crear algo redundante — igual criterio que con memoria y
   documentos: buscá antes de duplicar.
6. `flow_delete(code)` es irreversible (borra también el historial de
   ejecuciones) — confirmá con el usuario si hay dudas.

## Formato del YAML

```yaml
metadata:
  name: Procesamiento de incidencia
  category: incident          # informativo; la categoria real la decide el
                               # parametro `category` de flow_save
  description: Analizar y resolver incidencias reportadas por usuarios.
  author: jose

entry: analyze                 # unico punto de entrada -- un "saltar pasos"
                                # se modela como decision dentro del
                                # procedure, no como entry_points multiples

procedure:
  - id: analyze
    type: task                 # task | decision | delegate
    description: Analizar la incidencia.
    next: investigate

  - id: investigate
    type: task
    description: Investigar posibles causas.
    next: decision

  - id: decision
    type: decision
    description: Existe informacion suficiente?
    branches:                  # decision NUNCA lleva `next`, solo branches
      sufficient: resolve
      insufficient: investigate

  - id: resolve
    type: task
    description: Resolver la incidencia.
    checkpoint:                 # propiedad composable en cualquier step, no
                                 # un type aparte
      required: true
      prompt: "Confirmas que se cree el ticket de resolucion?"
      on_reject: investigate
    tools_allowed: [create_ticket]
    next: end

  - id: end
    type: task
    description: Cierre del flujo.
    terminal: true              # marca explicita -- nunca se infiere el fin
                                 # por ausencia de next

rules:
  - id: R001
    rule: No asumir informacion que no este presente en el contexto.
    applies_to: [investigate, decision]   # vacio/omitido = aplica a todo

tools:
  - search_incidents
  - create_ticket

execution_policy:
  autonomy: guided
  confirmation_required: [create_ticket]
```

Reglas de validez (lo que `flow_validate` chequea, con el step/campo exacto
en el mensaje si falla):

- `entry` debe existir en `procedure`, y cada `id` de step debe ser único.
- Todo `next`, `branches.<condicion>` y `checkpoint.on_reject` debe apuntar a
  un `id` real del `procedure` — nada de referencias colgantes.
- Un step `decision` **debe** tener `branches` (no vacío) y **no puede**
  tener `next`. Un step `task`/`delegate` no-terminal **debe** tener `next`.
- Un step `terminal: true` no puede tener `next` ni `branches`, y el
  `procedure` necesita al menos un step terminal (si no, el flujo nunca
  cierra).
- `type: delegate` requiere el bloque `delegate: {agent_type, prompt_ref?,
  parallel_group?}`; ese bloque no aplica a `task`/`decision`. Dos o más
  steps `delegate` que comparten `parallel_group` deben apuntar todos al
  mismo `next` (se revelan juntos en una sola respuesta de `flow_next`, para
  representar trabajo concurrente — p.ej. un auditor y un calificador
  trabajando en paralelo).
- `rules[].applies_to`, si lo usás, debe referenciar steps que existan.

Detalle de YAML que conviene saber: `cerebro-flows` normaliza el parser para
que `yes`/`no` (branches de una decisión sí/no, el caso más natural del
mundo) se guarden como los strings literales "yes"/"no", NO como booleanos —
podés usarlos con confianza en `branches` sin comillas. `true`/`false` sí se
interpretan como booleanos de verdad (los usás en `terminal` y
`checkpoint.required`).

## Errores y honestidad

Mismo criterio que el resto de cerebro (ver `SKILL.md`): un `error` se
reporta tal cual; nunca inventes en qué paso está un flujo si no viene de
`flow_next`/`flow_start`/el estado real de un `run_id`.
