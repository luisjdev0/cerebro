# KnowledgeOS — Plan v2

## Memoria contextual persistente para agentes de IA

**Estado:** Borrador de arquitectura y roadmap ejecutable
**Reemplaza a:** `plan.md` (v0.2)
**Fecha:** 2026-08-06

---

## 1. Resumen

KnowledgeOS es una capa de memoria persistente, self-hosted y agnóstica al modelo, que permite a cualquier agente de IA (Claude, GPT, Gemini, agentes propios) consultar y escribir sobre un mismo cuerpo de conocimiento del usuario: quién es, en qué proyectos trabaja, qué decisiones ha tomado y qué procedimientos sigue.

La premisa se mantiene de v1:

> El conocimiento pertenece al usuario, no al modelo.

Lo que cambia en v2 es el **cómo**: este plan prioriza validar la hipótesis central con el mínimo de infraestructura, define criterios de éxito medibles antes de añadir capas, y pospone las piezas caras (modelo auxiliar local, grafo completo, conectores) hasta que los datos reales justifiquen su existencia.

### La hipótesis central a validar

La apuesta diferencial de KnowledgeOS no es "búsqueda vectorial sobre notas" (eso es un RAG genérico y ya existe de sobra). Es esta:

> La recuperación de memoria personal falla principalmente por **contaminación entre contextos** (ej. "Expense Tracker" el proyecto vs. "finanzas personales" el dominio), y un sistema que modele contextos como ciudadanos de primera clase — con aislamiento, desambiguación y aprendizaje de preferencias — recupera mejor que similitud semántica pura.

Todo el roadmap está ordenado para probar o refutar esto lo antes posible.

---

## 2. Lecciones incorporadas desde v1

Cambios de criterio respecto al plan anterior, con su justificación:

| # | Decisión v1 | Decisión v2 | Por qué |
|---|---|---|---|
| 1 | Se asumía que no existía nada parecido | **Fase 0 obligatoria**: evaluar mem0, Zep/Graphiti y Letta contra nuestros casos de prueba antes de escribir código propio | Existen proyectos activos que cubren gran parte del terreno. Si uno resuelve el caso de aislamiento de contexto, adaptamos en vez de construir. Si fallan justo ahí, tenemos la justificación documentada. |
| 2 | Modelo auxiliar local (Ollama + Qwen/Llama/DistilBERT) desde el diseño base | Desambiguación vía el **modelo principal ya presente en la conversación** (prompt corto con contextos candidatos). Modelo local diferido a fase 4, condicionado a datos | Un clasificador zero-shot de 1-3B sin fine-tuning rinde mal justo en los casos sutiles que nos importan. Sin dataset real de ambigüedades no hay con qué entrenarlo ni contra qué medirlo. Además compite por RAM con el resto del stack. |
| 3 | PostgreSQL + Qdrant + Redis desde v0.1 | **Solo PostgreSQL + pgvector**. Qdrant/Redis solo si las mediciones lo exigen | Tres bases de datos para un usuario es superficie operativa sin retorno. pgvector con HNSW cubre decenas de miles de memorias con latencias de un dígito de ms. |
| 4 | Roadmap por features (v0.1 → v1.0) | Roadmap por **fases con criterio de salida medible** | "v0.3: Knowledge Graph" no dice cuándo está listo ni si aportó valor. Cada fase de v2 define qué medición debe mejorar para justificar la siguiente. |
| 5 | Sin política de olvido ni conflictos (solo `memory.forget()` manual) | **Ciclo de vida explícito**: versionado, supersedencia, decaimiento de relevancia | "Producción usa Ubuntu 24" será falso algún día. Un sistema de memoria sin política de obsolescencia acumula mentiras con embedding. |
| 6 | Seguridad = no guardar secretos | Se mantiene, y se añade: **auth por agente, audit log, cifrado en reposo** | La premisa es "el conocimiento es del usuario"; eso obliga a saber qué agente leyó/escribió qué y cuándo. |
| 7 | Sin criterio de evaluación | **Suite de evaluación de retrieval** como artefacto de primera clase, creada en Fase 0 | Sin casos de prueba no se puede afirmar que el Context Engine mejora nada. |

---

## 3. Fase 0 — Validación (antes de construir)

**Duración estimada:** 3–5 días. **No se escribe código de producto en esta fase.**

### 3.1 Suite de evaluación (el artefacto más importante del proyecto)

Crear `evals/cases.yaml` con 30–50 casos reales del propio usuario, cada uno con:

```yaml
- id: amb-001
  query: "¿Cuánto gasté este mes?"
  memorias_relevantes: [finanzas-gastos-mensuales]
  memorias_trampa: [expense-tracker-calculo]   # similares semánticamente, contexto incorrecto
  contexto_esperado: finanzas_personales
```

Tres categorías de casos:

1. **Directos** — una sola memoria relevante, sin ambigüedad (línea base; cualquier RAG debe pasarlos).
2. **Ambiguos** — dos o más contextos plausibles comparten vocabulario (el corazón del proyecto).
3. **Temporales/conflictivos** — la respuesta correcta depende de cuál versión de una memoria está vigente.

Métricas: precision@5, recall@5, y **tasa de contaminación** (¿cuántas memorias trampa entraron al contexto final?). Esta última es la métrica firma del proyecto.

### 3.2 Benchmark de alternativas existentes

Correr la suite contra, mínimo:

- **mem0** (self-hosted) — memoria por usuario con extracción automática.
- **Zep / Graphiti** — grafo de conocimiento temporal.
- **Letta (MemGPT)** — memoria jerárquica de agente.

**Decisión al final de Fase 0** (documentar en `decisions/000-build-vs-adopt.md`):

- Si alguna alternativa logra tasa de contaminación aceptable en los casos ambiguos → **adoptar/extender** y este plan se convierte en un plan de integración.
- Si todas fallan en aislamiento de contexto → **construir**, y el benchmark queda como evidencia y como vara de comparación permanente.

> Nota honesta: aunque el resultado sea "construir", conviene robar ideas con nombre y apellido de esos proyectos — el modelo temporal de Graphiti y la extracción automática de mem0 son directamente relevantes.

---

## 4. Arquitectura objetivo

Misma visión de v1, con las capas reordenadas por cuándo se ganan el derecho a existir:

```
Agente (Claude / GPT / Gemini / custom)
        │
        ▼
   MCP Server  ──────────────  (Fase 1)
        │
        ▼
  Memory Gateway (API HTTP interna)
        │
        ├─► Retrieval híbrido: vector + keyword + filtros  (Fase 1)
        ├─► Context Engine: scoping y desambiguación        (Fase 2)
        ├─► Relaciones / grafo ligero                       (Fase 3)
        └─► Clasificador local opcional                     (Fase 4, condicionado)
        │
        ▼
  PostgreSQL + pgvector   (única base de datos hasta que las mediciones digan lo contrario)
```

### 4.1 Principios (heredados de v1, siguen vigentes)

- **Model agnostic** — los modelos son clientes; MCP es la interfaz.
- **Self-hosted** — datos, infra y seguridad bajo control del usuario.
- **Context first** — la recuperación entiende quién/qué/cuándo/dónde, no solo similitud.
- **Memory over conversation** — se almacena conocimiento destilado, no transcripciones.

### 4.2 Principio nuevo en v2

- **Earn your complexity** — ninguna capa entra a la arquitectura sin una medición de la suite de evaluación que demuestre qué mejora. Esto aplica al grafo, al modelo local, a Qdrant, a Redis y a los conectores.

---

## 5. Modelo de datos

### 5.1 Tablas núcleo (Fase 1)

```sql
-- Contextos: la unidad de aislamiento. Ciudadano de primera clase.
CREATE TABLE contexts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        TEXT UNIQUE NOT NULL,        -- "expense-tracker", "finanzas-personales"
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,               -- project | domain | person | org
    description TEXT,                        -- usado por el LLM para desambiguar
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE memories (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    context_id   UUID NOT NULL REFERENCES contexts(id),
    type         TEXT NOT NULL,              -- semantic | episodic | procedural | decision
    title        TEXT NOT NULL,
    content      TEXT NOT NULL,
    importance   REAL DEFAULT 0.5,           -- 0..1
    confidence   REAL DEFAULT 0.8,           -- 0..1
    source       TEXT,                       -- agente/canal que la creó
    status       TEXT DEFAULT 'active',      -- active | superseded | archived
    superseded_by UUID REFERENCES memories(id),
    occurred_at  TIMESTAMPTZ,               -- cuándo ocurrió el hecho (episódicas)
    created_at   TIMESTAMPTZ DEFAULT now(),
    updated_at   TIMESTAMPTZ DEFAULT now(),
    embedding    vector(1024)               -- dimensión según modelo de embeddings elegido
);

CREATE INDEX ON memories USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON memories USING gin (to_tsvector('spanish', title || ' ' || content));

-- Audit log: qué agente hizo qué. Base de la historia de seguridad.
CREATE TABLE audit_log (
    id         BIGSERIAL PRIMARY KEY,
    agent      TEXT NOT NULL,
    action     TEXT NOT NULL,                -- search | remember | update | forget
    memory_id  UUID,
    detail     JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);
```

Notas de diseño:

- **`contexts` es tabla, no tag.** Este es el cambio estructural que encarna la hipótesis: el aislamiento no es un filtro opcional sobre metadata, es la partición primaria del espacio de memoria. Toda búsqueda opera *dentro* de un scope de contextos resuelto previamente.
- **Los cuatro tipos de memoria de v1 se conservan** (semántica/episódica/procedural/decisión) — esa taxonomía era de lo mejor del plan original. Se implementan como columna `type`, no como tablas separadas: comparten ciclo de vida y retrieval.
- **`superseded_by` implementa el versionado** que v1 mencionaba pero nunca definía: actualizar una memoria crea una fila nueva y marca la anterior como `superseded`. Nada se borra silenciosamente; el retrieval por defecto solo ve `active`.
- Relaciones entre memorias (grafo) **no aparecen todavía** — llegan en Fase 3 como tabla de aristas, si la evaluación lo justifica (§8, Fase 3).

### 5.2 Ciclo de vida de una memoria

```
                remember()
                    │
                    ▼
                 active ──────── update() ────► nueva fila active
                    │                             (anterior → superseded)
     sin acceso + baja importancia
     durante N meses │
                    ▼
                archived      (recuperable, excluida del retrieval por defecto)
                    │
              forget() explícito
                    ▼
                 borrado      (fila eliminada; queda rastro en audit_log)
```

- **Decaimiento:** un job periódico calcula relevancia efectiva = `importance × factor_recencia(último_acceso)`. Memorias episódicas decaen más rápido que semánticas; las de tipo `decision` no decaen (una decisión vieja sigue siendo la decisión tomada).
- **Conflictos:** si `remember()` recibe contenido que contradice una memoria activa del mismo contexto (detectado por alta similitud + señal del extractor), no sobreescribe: crea la nueva, marca la vieja `superseded` y registra el par en el audit log. Si la contradicción es dudosa, se guarda con `confidence` baja y se deja que la desambiguación en lectura pregunte al usuario.

### 5.3 Concurrencia multi-agente

Regla simple para empezar: **última escritura gana a nivel de fila, pero nunca destruye** — como toda actualización es una fila nueva con supersedencia, dos agentes escribiendo "lo mismo" a la vez producen dos versiones encadenadas, no una pérdida. El audit log permite reconstruir quién escribió qué. Locking optimista formal solo si aparece un caso real que lo necesite.

---

## 6. API y herramientas MCP

Superficie mínima en Fase 1, la misma familia que v1 pero recortada:

| Tool MCP | Descripción |
|---|---|
| `memory_search(query, context?, type?, limit?)` | Retrieval híbrido. Si `context` se omite, entra el flujo de scoping (§7). |
| `memory_remember(content, context, type, importance?)` | Alta. El contexto es **obligatorio** al escribir: la ambigüedad se paga una vez en escritura, no en cada lectura. |
| `memory_update(id, content)` | Crea versión nueva + supersede la anterior. |
| `memory_forget(id)` | Archiva (borrado real solo con `hard=true`). |
| `memory_contexts()` | Lista contextos con descripciones — es lo que el agente usa para clasificar dónde escribir y como candidatos de desambiguación. |

Diferidas a fases posteriores: `memory_related()` y `memory_timeline()` (Fase 3, requieren aristas), `memory_recent()` (azúcar, cuando haya uso real).

El MCP server es un adaptador delgado sobre la API HTTP interna; toda la lógica vive en la API para que futuros clientes no-MCP (CLI, webhooks, UI) usen exactamente el mismo camino.

---

## 7. Context Engine (Fase 2) — desambiguación sin modelo local

El flujo de v1 (Fast Router → ¿ambigüedad? → resolver) se conserva; cambia quién resuelve.

```
query (sin contexto explícito)
        │
        ▼
  Scoring barato y determinista:
  overlap léxico + histórico de preferencias + distribución
  de similitud vectorial entre contextos
        │
   ¿un contexto domina claramente?
        │
   sí ──┴── no
   │         │
   ▼         ▼
buscar    devolver al agente llamante los contextos candidatos
en ese    con sus descripciones → el LLM de la conversación decide
contexto  (o pregunta al usuario si ni él puede)
```

Puntos clave:

1. **El "modelo auxiliar" de la Fase 2 es el modelo que ya está en la conversación.** El MCP server devuelve un resultado estructurado tipo `ambiguous: [candidatos]` y el agente (Claude/GPT), que ya tiene el contexto conversacional completo — algo que un clasificador aislado nunca tendrá — elige o repregunta. Costo marginal ~cero, cero infraestructura nueva, y de paso mejor información para decidir.
2. **Cada resolución se registra** en `disambiguation_log` (query, candidatos, elección, quién eligió). Esto construye dos activos: las *preferencias aprendidas* de v1 ("gastos en contexto personal → finanzas_personales") implementadas como reglas consultables por el scorer, y el **dataset de entrenamiento** que la Fase 4 necesitaría.
3. **Privacidad:** en este diseño la clasificación no exporta contenido de memorias — solo nombres/descripciones de contextos y la query, que el modelo de la conversación ya conocía. El principio self-hosted aplica con toda su fuerza al almacenamiento; este paso no lo compromete.

---

## 8. Roadmap por fases con criterios de salida

### Fase 0 — Validación *(3–5 días)*

Suite de evaluación + benchmark de mem0/Graphiti/Letta + decisión build-vs-adopt documentada.
**Criterio de salida:** `decisions/000-build-vs-adopt.md` escrito, con números de la suite para cada alternativa.

### Fase 1 — Núcleo útil *(2–3 semanas)*

PostgreSQL + pgvector, API HTTP, MCP server con las 5 tools, retrieval híbrido (vector + full-text + filtro por contexto/tipo/status), ciclo de vida con supersedencia, audit log, Docker Compose de 2 servicios (db + api/mcp).
**Criterio de salida:** los casos *directos* de la suite pasan con precision@5 ≥ 0.9, y el sistema está en uso diario real del propio usuario (dogfooding desde la semana 1 — sin uso real no se genera el dataset que alimenta todo lo demás).

### Fase 2 — Context Engine *(2–3 semanas)*

Scoring de contextos, protocolo de ambigüedad hacia el agente, `disambiguation_log`, reglas de preferencia aprendidas.
**Criterio de salida:** tasa de contaminación en casos *ambiguos* de la suite reducida a menos de la mitad respecto a Fase 1. Si no mejora, la hipótesis central está en duda y se replantea antes de seguir.

### Fase 3 — Relaciones *(condicionada)*

Tabla de aristas (`memory_edges: from, to, relation`), expansión de retrieval a un salto de grafo, `memory_related()` y `memory_timeline()`.
**Entra solo si** el análisis de fallos de Fase 2 muestra casos que la desambiguación por contexto no resuelve pero una relación explícita sí. Empezar con grafo ligero en Postgres; nada de base de grafos dedicada.

### Fase 4 — Clasificador local *(condicionada)*

Fine-tuning de un modelo pequeño (Qwen 2.5 1.5B o similar vía Ollama) sobre el `disambiguation_log` acumulado, para resolver localmente lo que hoy se delega al modelo de la conversación.
**Entra solo si** se cumplen las dos: (a) hay ≥ ~500 resoluciones registradas para entrenar/evaluar, y (b) hay una razón medida — latencia, costo o una política de privacidad estricta de "ni la query sale del VPS". Esta fase es la versión honesta del "modelo auxiliar" de v1: misma idea, pero con datos para entrenarla y una vara para saber si funciona.

### Fase 5 — Conectores e ingesta *(post-v1.0, uno a la vez)*

Ingesta desde fuentes externas (GitHub, Obsidian, Notion, Calendar…). Cada conector es un mini-proyecto con mantenimiento propio; se priorizan por dolor real, no se prometen en bloque. Candidato natural al primero: importador de `MEMORY.md`/`CLAUDE.md` existentes, que es barato y resuelve la migración del statu quo.

### v1.0 =

Fases 1 y 2 sólidas + evaluación pasando + dogfooding sostenido durante ≥ 1 mes. El grafo, el modelo local y los conectores son *mejoras* de v1.0, no requisitos.

---

## 9. Seguridad

- **Secretos:** igual que v1 — nunca se almacenan valores; solo referencias (`secret://production/aws`). El extractor de `remember()` rechaza contenido que matchee patrones de credenciales (claves AWS, tokens, cadenas de conexión) y responde con el formato de referencia correcto.
- **Autenticación por agente:** cada cliente MCP/API tiene su token con identidad propia (`claude-desktop`, `automation-x`). Sin token, sin acceso. La identidad alimenta `source` y el audit log.
- **Autorización (v1.0):** scopes simples por token: lectura/escritura y opcionalmente restricción por contextos (un agente de trabajo no lee `finanzas-personales`).
- **Audit log** desde Fase 1 — es una tabla y un middleware, y es la materialización de "el conocimiento pertenece al usuario": el usuario puede ver qué agente tocó qué.
- **Cifrado:** TLS en tránsito siempre; en reposo, cifrado de disco a nivel de VPS como línea base (cifrado a nivel de aplicación queda explícitamente fuera de alcance hasta que haya multiusuario).
- **Backups:** `pg_dump` diario automatizado + restore probado. Un sistema cuyo pitch es "memoria persistente" no puede perder la memoria.

---

## 10. Despliegue

```
VPS (4 vCPU / 8 GB RAM / 100 GB SSD)   ← el mismo target de v1, ahora sobrado
│
├─ docker compose
│   ├─ postgres:17 + pgvector
│   └─ knowledgeos (API + MCP server, un solo proceso)
│
└─ backups → almacenamiento externo
```

Embeddings: empezar con un modelo de embeddings servido por API (barato, sin RAM local) **o** un modelo pequeño local tipo `bge-m3` si se prefiere cero salida de datos — es la única decisión de privacidad real de la Fase 1, y es intercambiable después (re-embed batch de toda la tabla es asumible a esta escala). Ollama, Qdrant y Redis no aparecen en el compose hasta que alguna fase condicionada los gane.

---

## 11. Riesgos principales

| Riesgo | Mitigación |
|---|---|
| Las alternativas existentes ya resuelven el problema | Fase 0 lo detecta en días, no en meses. Adoptar no es fracasar. |
| La hipótesis de contexto no mejora el retrieval | El criterio de salida de Fase 2 lo hace visible y detiene la inversión a tiempo. |
| El sistema no se usa a diario (sin dogfooding no hay dataset ni señal) | Dogfooding es criterio de salida de Fase 1, no un deseo. Importador de MEMORY.md baja la fricción inicial. |
| Acumulación de memorias obsoletas/contradictorias | Supersedencia + decaimiento + casos temporales en la suite de evaluación. |
| Scope creep (grafo, modelo local, conectores "porque el diseño los menciona") | Principio *earn your complexity*: cada capa condicionada a una medición. |

---

## 12. Qué se conserva de v1, explícitamente

Para que no se pierda en la reescritura — v1 acertó en:

- El planteamiento del problema (fragmentación MEMORY.md / Notion / Obsidian / wikis) y el ejemplo canónico de ambigüedad Expense Tracker vs. finanzas personales, que ahora es el caso `amb-001` de la suite.
- La taxonomía de cuatro tipos de memoria.
- Los cuatro principios de diseño (model-agnostic, self-hosted, context-first, memory-over-conversation).
- La intuición del patrón router-rápido → escalar-solo-si-ambiguo (se conserva; solo cambia el resolvedor).
- El posicionamiento final: esto no compite en "¿qué documentos son similares?" sino en "¿qué conocimiento de mi vida es relevante para esta situación?".

Lo que v2 añade es disciplina de ejecución: medir antes de construir, construir lo mínimo que valida, y dejar que los datos — no el documento de arquitectura — decidan qué capa entra después.
