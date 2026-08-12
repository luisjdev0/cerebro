# Suite de evaluación de retrieval — KnowledgeOS

## Propósito

KnowledgeOS es un sistema de memoria persistente para agentes de IA. Antes de construir
o adoptar cualquier motor de retrieval (embeddings, grafos de conocimiento, búsqueda
híbrida, etc.) necesitamos poder responder dos preguntas de forma objetiva y repetible:

1. **¿Encuentra lo que debe encontrar?** — cuando el usuario pregunta algo, ¿el sistema
   recupera las memorias relevantes en el top-k?
2. **¿Se contamina entre contextos?** — el usuario típico tiene varios "mundos" de vida
   (proyectos de software, clientes, finanzas personales, salud, aprendizaje...). Cuando
   pregunta algo de un contexto, ¿el sistema le devuelve memorias de otro contexto que
   simplemente comparten vocabulario?

El caso canónico que motiva esta suite: el usuario tiene un proyecto de software
"Expense Tracker" y un dominio personal "finanzas personales". Ante la pregunta
**"¿cuánto gasté este mes?"**, solo deberían recuperarse memorias de finanzas
personales — las memorias del proyecto de software (que hablan de "gastos" y "costos"
en el sentido de una tabla de datos o una factura de hosting) son **memorias trampa**:
técnicamente relevantes por palabra clave, pero semánticamente del contexto equivocado.

Esta suite no evalúa razonamiento, redacción de respuestas ni generación — solo
**retrieval puro**: dado un adaptador de memoria y una query, ¿qué ids devuelve y en
qué orden?

## Estructura

```
evals/
├── README.md               este archivo
├── memories.yaml            corpus sintético (~40 memorias en 6 contextos)
├── cases.yaml                30 casos de prueba contra ese corpus
├── harness/
│   ├── run_eval.py           runner del harness (CLI)
│   ├── base.py                interfaz MemoryAdapter
│   ├── requirements.txt       (solo pyyaml)
│   └── adapters/
│       ├── __init__.py        registro de adaptadores (ADAPTERS)
│       ├── naive_keyword.py   adaptador de referencia (baseline)
│       └── TEMPLATE.py        esqueleto para adaptadores nuevos (mem0/graphiti/letta/...)
└── results/
    └── <adapter>-<timestamp>.json   detalle de cada corrida (se genera al ejecutar)
```

## El corpus (`memories.yaml`)

~40 memorias sintéticas de un desarrollador freelance hispanohablante, repartidas en
6 contextos:

| Contexto              | Tipo       | Descripción                                    |
|------------------------|------------|------------------------------------------------|
| `expense-tracker`      | proyecto   | app de finanzas que el usuario desarrolla       |
| `finanzas-personales`  | dominio    | las finanzas reales del usuario                 |
| `cliente-acme`         | proyecto   | proyecto freelance para el cliente "Acme"       |
| `infraestructura`      | dominio    | su VPS, despliegues, DNS, backups               |
| `salud`                | dominio    | ejercicio, alergias, chequeos médicos           |
| `aprendizaje`          | dominio    | cursos, lecturas, certificaciones               |

Cada memoria tiene `id`, `context`, `type` (semantic/episodic/procedural/decision),
`title`, `content` y `status` (`active` o `superseded`).

El corpus incluye **colisiones léxicas deliberadas** entre contextos, para forzar a
cualquier sistema de retrieval basado en palabras clave (o en embeddings poco
discriminativos) a equivocarse si no aísla bien por contexto:

- **"gastos" / "costos"** — entre `expense-tracker` (costos de hosting, tabla de
  `gastos` en el modelo de datos) y `finanzas-personales` (gastos reales del usuario).
- **"migración" / "migrar"** — entre `infraestructura` (migración del SO de producción,
  migrar servicios a Docker) y `cliente-acme` (migración de la base de datos del
  cliente).
- **"presupuesto"** — entre `cliente-acme` (presupuesto aprobado del proyecto) y
  `finanzas-personales` (presupuesto mensual personal).

También incluye **3 pares "superseded → active"**, hechos que cambiaron con el tiempo:

- `inf-produccion-os-antiguo` (Ubuntu 22.04, superseded) → `inf-produccion-os-nuevo`
  (Ubuntu 24.04, active)
- `ca-tarifa-hora-antigua` ($35/h, superseded) → `ca-tarifa-hora-nueva` ($45/h, active)
- `fp-presupuesto-antiguo` (1,500/mes, superseded) → `fp-presupuesto-mensual`
  (2,000/mes, active)

Por defecto el harness **solo inserta memorias `active`** — las `superseded` existen
para probar deliberadamente si un sistema devuelve un hecho viejo cuando ya no
debería (usar `--include-superseded`, ver más abajo).

## Los casos (`cases.yaml`)

30 casos, cada uno con: `id`, `categoria`, `query` (en español, natural),
`memorias_relevantes` (ids que deberían salir en el top-k), `memorias_trampa` (ids que
NO deberían salir, puede ser una lista vacía) y `contexto_esperado`.

### 1. Directos (12 casos, prefijo `d-`)

Una sola memoria relevante, sin ambigüedad de vocabulario relevante. `memorias_trampa`
va vacío (o casi). Miden el caso base: "¿el sistema encuentra lo obvio?".

Ejemplo: `d-004` — *"¿En qué proveedor está alojado mi VPS personal?"* →
`inf-vps-proveedor`.

### 2. Ambiguos (12 casos, prefijo `amb-`)

La query comparte vocabulario con memorias de **dos o más contextos distintos**.
`memorias_trampa` documenta explícitamente cuáles memorias, si aparecen en el top-k,
cuentan como contaminación. Es la categoría central de la suite — mide si el sistema
distingue contexto más allá de la superficie léxica.

El primer caso ambiguo es `amb-001`, el caso canónico del proyecto:

```yaml
- id: amb-001
  categoria: ambiguo
  query: "¿Cuánto gasté este mes?"
  memorias_relevantes: [fp-resumen-gastos-mes]
  memorias_trampa: [et-modelo-datos-gastos, et-costos-hosting]
  contexto_esperado: finanzas-personales
```

### 3. Temporales (6 casos, prefijo `tmp-`)

La respuesta correcta es la memoria **activa** vigente; su versión `superseded` es la
trampa. Miden si el sistema entiende que un hecho fue reemplazado, no solo que
"algo relacionado" existe. Esta categoría solo es significativa si el corpus se
indexa con `--include-superseded` (ver abajo) — si no, la memoria trampa ni siquiera
está en el índice y la contaminación será trivialmente 0%.

## Métricas

Para cada caso, con `k=5` (configurable con `--k`):

- **Precision@5** = (memorias relevantes en el top-5) / 5. Sigue la convención
  estándar de IR: si el adaptador devuelve menos de 5 resultados, las posiciones
  vacías cuentan como "no relevante" (no se reduce el denominador).
- **Recall@5** = (memorias relevantes en el top-5) / (total de memorias relevantes
  del caso). En esta suite casi todos los casos tienen una sola memoria relevante,
  así que recall@5 es binario (0% o 100%) por caso.
- **Tasa de contaminación** = fracción de casos, dentro de una categoría, donde
  **al menos una** memoria trampa aparece en el top-5. Es la métrica más importante
  de este proyecto: mide fugas de contexto, no solo si "se encontró algo relevante".

El runner agrega estas tres métricas por categoría (directo/ambiguo/temporal) y en
total.

## Cómo correr el harness

```bash
# instalar la única dependencia
python -m pip install -r evals/harness/requirements.txt

# correr con el adaptador de referencia (baseline naive por keywords)
python evals/harness/run_eval.py --adapter naive

# incluir las memorias superseded (para que la categoría "temporal" sea significativa)
python evals/harness/run_eval.py --adapter naive --include-superseded

# top-k distinto, no guardar el JSON, apuntar a otro corpus, etc.
python evals/harness/run_eval.py --adapter naive --k 10 --no-save
python evals/harness/run_eval.py --adapter naive --memories otro_corpus.yaml --cases otros_casos.yaml
```

Cada corrida imprime una tabla con precision@k, recall@k y tasa de contaminación por
categoría, y (a menos que uses `--no-save`) guarda el detalle completo por caso en
`evals/results/<adapter>-<timestamp>.json`.

### Qué esperar del adaptador `naive`

`NaiveKeywordAdapter` (en `evals/harness/adapters/naive_keyword.py`) rankea memorias
por overlap de tokens normalizados (sin stopwords españolas básicas), sin ningún
concepto de contexto ni de tiempo. Es una línea base deliberadamente pobre para
verificar que el harness completo corre sin instalar nada más pesado, y para tener un
punto de comparación "peor caso razonable".

Se espera (y es correcto/deseable) que tenga:

- Precision@5 y recall@5 razonables en `directo` (encuentra lo obvio).
- **Contaminación alta en `ambiguo`** — por diseño, no distingue contexto, así que las
  colisiones léxicas del corpus (gastos/costos, migración, presupuesto) lo engañan.
- **Contaminación alta en `temporal` cuando se corre con `--include-superseded`** — no
  tiene noción de "vigente vs. reemplazado", así que memorias viejas compiten en pie
  de igualdad con las nuevas.

Cualquier adaptador real (mem0, graphiti, letta, o el motor propio de KnowledgeOS)
debería superar claramente al naive, sobre todo en tasa de contaminación de `ambiguo`
y `temporal` — esa es la señal de que el sistema realmente aísla contexto y entiende
vigencia temporal, no solo que "busca bien".

## Cómo añadir casos propios

1. Si necesitas una memoria nueva, añádela a `evals/memories.yaml` con un `id` único
   (slug, prefijo por contexto), `context`, `type`, `title`, `content` y `status`.
2. Añade el caso a `evals/cases.yaml`:
   - `id`: prefijo por categoría (`d-`, `amb-`, `tmp-`) seguido de un número.
   - `categoria`: `directo`, `ambiguo` o `temporal`.
   - `query`: en español, como la escribiría un usuario real.
   - `memorias_relevantes`: ids que deberían salir en el top-k.
   - `memorias_trampa`: ids de contexto equivocado (o la versión superseded, en
     temporales) que NO deberían salir. Vacío en casos directos sin riesgo real de
     colisión.
   - `contexto_esperado`: el `context` al que pertenece la respuesta correcta.
3. Corre el harness (`python evals/harness/run_eval.py --adapter naive`) — el runner
   valida automáticamente que todos los ids referenciados en `cases.yaml` existan en
   `memories.yaml` y avisa (sin abortar) si falta alguno.
4. Si estás probando colisiones léxicas nuevas, revisa que la memoria "trampa" y la
   memoria "relevante" compartan vocabulario real (no solo el mismo contexto temático)
   — esa es la señal que hace al caso útil como ambiguo.

## Cómo añadir un adaptador nuevo (mem0, graphiti, letta, KnowledgeOS real, ...)

1. Copia `evals/harness/adapters/TEMPLATE.py` a
   `evals/harness/adapters/<nombre_sistema>.py` y renombra la clase.
2. Implementa `setup()`, `insert(memory)`, `search(query, k)` y `teardown()` según la
   interfaz `MemoryAdapter` (`evals/harness/base.py`). El contrato clave:
   `search()` debe devolver los `id` del corpus (no ids internos del sistema, no
   objetos), en orden de relevancia descendente.
3. Regístralo en `evals/harness/adapters/__init__.py` (diccionario `ADAPTERS`).
4. Corre `python evals/harness/run_eval.py --adapter <nombre_sistema>`.

`TEMPLATE.py` incluye TODOs y docstrings con ejemplos concretos de qué llamar en
mem0, graphiti y letta — es solo el esqueleto, no una implementación real.
