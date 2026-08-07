# Reporte final de evaluación — KnowledgeOS v1.0

**Fecha:** 2026-08-07
**Suite:** 40 memorias sintéticas / 6 contextos / 30 casos (12 directos, 12 ambiguos, 6 temporales), k=5, con `--include-superseded`.

## La métrica firma: tasa de contaminación

Fracción de casos donde al menos una "memoria trampa" (relevante semánticamente pero
de contexto incorrecto) entra al top-5. Es la métrica que motivó el proyecto: el recall
nunca fue el problema (100% en todos los sistemas medidos); el problema es *qué más*
se cuela junto a la memoria correcta.

## Evolución por fase

| Sistema | Ambiguos | Directos | Temporales | Total |
|---|---:|---:|---:|---:|
| Baseline naive (keywords) | 50.0% | 0% | 83.3% | 36.7% |
| Fase 1 — retrieval híbrido (`scope=all`) | 25.0% | 0% | 0%¹ | 10.0% |
| Fase 2+ — Context Engine (`scope=auto`) | **0%** | **0%** | **0%** | **0%** |

Recall@5 = 100% en los tres sistemas y las tres categorías (nunca se degradó).
Precision@5 ≈ 20% por diseño del corpus (1 memoria relevante / k=5); no es informativa aquí.

¹ La mejora en temporales combina dos cosas: el modelo de supersedencia real (las
versiones obsoletas no se indexan como activas) y el retrieval híbrido. El 83.3% del
baseline corresponde a indexar ambas versiones como activas sin relación entre sí.

## Qué produjo cada salto

- **50% → 25%** (Fase 1): retrieval híbrido — embeddings multilingües + full-text
  español fusionados con RRF discriminan mejor que overlap léxico, incluso sin saber
  el contexto. Supersedencia real eliminó la contaminación temporal.
- **25% → 0%** (Fase 2): Context Engine — scoring determinista de contextos
  (agregado RRF por contexto + preferencias aprendidas + mención explícita) con
  decisión por dominancia/margen. Cuando ningún contexto domina, el sistema **no
  mezcla**: devuelve candidatos con evidencia y deja la elección al agente, y esa
  elección se registra y alimenta el aprendizaje (`context_preferences`).
- **Verificado sin regresión** en cada fase posterior (3, 4, 5 y productización):
  el 0% se reprodujo en frío (truncando log de desambiguaciones y preferencias) en
  cada corrida de control.

## Calibración y advertencias honestas

- Los umbrales del engine (`CONTEXT_ENGINE_DOMINANCE_THRESHOLD=0.45`,
  `MARGIN_THRESHOLD=0.25`, `PREFERENCE_BOOST_PER_WEIGHT=0.008`) se calibraron en 2
  iteraciones **contra este corpus sintético de 30 casos**. El 0% es el techo de esta
  suite, no una garantía universal: datos reales de uso diario pueden requerir
  re-ajuste — para eso existen las preferencias aprendidas y `GET /stats`.
- Hallazgos de la calibración que quedaron como diseño: el desempate del "agente
  razonable" usa el score agregado del candidato (el score individual top empata casi
  siempre por la discretización del RRF), y el boost por preferencias es
  deliberadamente pequeño para que un término genérico aprendido ("mes") no pueda
  tumbar la señal de retrieval con una sola resolución.
- El benchmark contra alternativas externas (mem0/Graphiti/Letta) quedó diferido con
  el esqueleto listo (`harness/adapters/TEMPLATE.py`); la decisión build se tomó con
  evidencia documental (`../decisions/000-build-vs-adopt.md`).

## Cómo reproducir

```bash
# Postgres arriba y API corriendo, luego:
python evals/harness/run_eval.py --adapter naive --include-superseded
KNOWLEDGEOS_SEARCH_SCOPE=all  python evals/harness/run_eval.py --adapter knowledgeos --include-superseded
KNOWLEDGEOS_SEARCH_SCOPE=auto python evals/harness/run_eval.py --adapter knowledgeos --include-superseded
```

Resultados detallados por caso en `results/*.json`.

## Próxima frontera de medición

El corpus sintético está saturado (0%). Las mejoras siguientes deben medirse con:
1. **Casos reales del usuario** añadidos a `cases.yaml` (ver README de esta carpeta).
2. **Dogfooding**: `knowledgeos stats` expone desambiguaciones auto vs. agente y
   preferencias aprendidas — la tasa de auto-resolución creciendo en el tiempo es la
   métrica de que el aprendizaje funciona en el mundo real.
3. Cuando existan ~500 desambiguaciones reales (`knowledgeos export-disambiguations`),
   evaluar el clasificador local (Fase 4) contra el flujo actual.
