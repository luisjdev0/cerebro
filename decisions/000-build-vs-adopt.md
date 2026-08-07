# Decisión 000 — Build vs. Adopt

**Fecha:** 2026-08-07
**Estado:** Aceptada
**Fase:** 0 (Validación)

## Decisión

**Construir KnowledgeOS**, con arquitectura propia para la capa de contextos y retrieval,
manteniendo a **Graphiti como candidato a motor de grafo para la Fase 3** (no se adopta ahora;
se reevalúa cuando la Fase 3 se gane su entrada según plan_v2 §8).

## Evidencia

### Documental (informe completo: `evals/research/alternativas.md`)

Ninguna de las tres alternativas evaluadas trata el aislamiento de contextos que comparten
vocabulario como problema de primera clase — en las tres, el particionamiento es
responsabilidad de la aplicación:

- **mem0**: filtros de metadata blandos (`user_id`/`agent_id`/`run_id`) sobre colección
  compartida; sin filtro explícito, todo se mezcla. Además eliminó Graph Memory de su SDK
  open source en 2026 (PR #4805) — señal de divergencia OSS vs. cloud.
- **Zep/Graphiti**: el mejor aislamiento estructural (`group_id` = subgrafos aislados) y el
  ciclo de vida más maduro (invalidación bi-temporal). Pero Zep Community Edition fue
  descontinuado en 2026; Graphiti solo es un motor de bajo nivel, no un producto de memoria —
  habría que construir encima de todos modos la capa de scoping, desambiguación y MCP propio.
- **Letta**: el más liviano de operar, pero aislamiento de grano grueso (un agente = un
  contexto) o manual vía tags; no hay desambiguación entre contextos.

### Empírica (línea base de la suite, `evals/results/`)

El baseline naive por keywords sobre el corpus de 40 memorias / 30 casos confirma que el
problema real es la contaminación, no el recall:

| Categoría | Recall@5 | Contaminación |
|---|---:|---:|
| directo | 100% | 0% |
| **ambiguo** | 100% | **50%** |
| temporal (con superseded indexadas) | 100% | 83% |

Encontrar la memoria correcta es trivial; evitar que se cuelen memorias de contextos ajenos
es el problema — exactamente la hipótesis del proyecto. Estos números son la vara que la
Fase 1 (retrieval con scoping por contexto) y la Fase 2 (Context Engine) deben batir.

### Benchmark empírico contra las alternativas: diferido, no descartado

No se implementaron los adaptadores de mem0/Graphiti/Letta en Fase 0: la evidencia
documental ya responde la pregunta de diseño (ninguna particiona por contexto de forma que
resuelva `amb-001` sin trabajo de aplicación equivalente al que vamos a construir), y correr
el benchmark requería instalar sus stacks + consumo de API de LLM para sus extractores.
El esqueleto (`evals/harness/adapters/TEMPLATE.py`) queda listo para implementarlos más
adelante como vara de comparación externa contra el adaptador propio de KnowledgeOS.

## Consecuencias

- Arranca la **Fase 1** según plan_v2 §8: PostgreSQL + pgvector, API, MCP server (5 tools),
  retrieval híbrido con scoping por contexto, ciclo de vida con supersedencia, audit log.
- Ideas adoptadas de la investigación: invalidación bi-temporal de Graphiti como referencia
  para nuestro modelo de supersedencia; el patrón `group_id` de subgrafos aislados como
  validación externa de "contexto como partición primaria, no como filtro".
- Criterio de éxito heredado: reducir la contaminación en ambiguos de 50% (baseline) a menos
  de la mitad al cierre de Fase 2.
