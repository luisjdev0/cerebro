#!/usr/bin/env python3
"""
Runner de la suite de evaluación de retrieval de cerebro-memory.

Uso:
    python evals/harness/run_eval.py --adapter naive
    python evals/harness/run_eval.py --adapter naive --include-superseded
    python evals/harness/run_eval.py --adapter naive --k 5 --results-dir evals/results

Requiere únicamente `pyyaml` (ver evals/harness/requirements.txt).

Qué hace:
  1. Carga evals/memories.yaml y evals/cases.yaml.
  2. Inserta en el adaptador seleccionado solo las memorias con status "active"
     (a menos que se pase --include-superseded, en cuyo caso se insertan todas).
  3. Para cada caso corre `adapter.search(query, k)` y calcula precision@k,
     recall@k y si el resultado quedó "contaminado" (alguna memoria trampa
     apareció en el top-k).
  4. Imprime una tabla agregada por categoría (directo/ambiguo/temporal) y
     guarda el detalle completo en evals/results/<adapter>-<timestamp>.json.

Ver evals/README.md para la definición completa de las métricas y cómo
interpretar los resultados.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parent
EVALS_DIR = HARNESS_DIR.parent

# En algunas consolas de Windows (cp1252/cp437) imprimir texto en español con
# tildes puede lanzar UnicodeEncodeError. Forzamos UTF-8 con reemplazo si el
# stream lo soporta; si no, seguimos con el encoding por defecto.
for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

# Permite `from base import MemoryAdapter` y `from adapters import ADAPTERS`
# sin necesidad de instalar el proyecto como paquete ni de correr con `-m`.
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR))

try:
    import yaml
except ImportError:
    print(
        "Falta pyyaml. Instálalo con:\n"
        "    python -m pip install -r evals/harness/requirements.txt",
        file=sys.stderr,
    )
    raise

from adapters import ADAPTERS  # noqa: E402
from base import MemoryAdapter  # noqa: E402  (re-exportado para adaptadores externos)


# ---------------------------------------------------------------------------
# Carga de datos
# ---------------------------------------------------------------------------

def load_memories(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    memories = data.get("memories", []) if data else []
    for m in memories:
        m.setdefault("status", "active")
    return memories


def load_cases(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("cases", []) if data else []


def validate_case_ids(cases: list[dict], known_ids: set[str]) -> None:
    """Avisa (sin abortar) si un caso referencia un id que no existe en el corpus."""
    for case in cases:
        referenced = set(case.get("memorias_relevantes", [])) | set(case.get("memorias_trampa", []))
        missing = referenced - known_ids
        if missing:
            print(
                f"[warn] caso '{case.get('id')}' referencia ids inexistentes en memories.yaml: "
                f"{sorted(missing)}",
                file=sys.stderr,
            )


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------

def precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """|relevantes en el top-k| / k. Sigue la convención estándar de IR: si el
    adaptador devuelve menos de k resultados, las posiciones faltantes cuentan
    como "no relevante" (no se reduce el denominador)."""
    if k == 0:
        return 0.0
    hits = sum(1 for doc_id in retrieved[:k] if doc_id in relevant)
    return hits / k


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float | None:
    """|relevantes en el top-k| / |relevantes totales|. None si el caso no
    define memorias relevantes (no debería pasar, pero se maneja con cuidado)."""
    if not relevant:
        return None
    hits = sum(1 for doc_id in retrieved[:k] if doc_id in relevant)
    return hits / len(relevant)


def is_contaminated(retrieved: list[str], trampa: set[str], k: int) -> bool:
    """True si alguna memoria trampa aparece en el top-k."""
    if not trampa:
        return False
    return any(doc_id in trampa for doc_id in retrieved[:k])


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run(adapter: MemoryAdapter, memories: list[dict], cases: list[dict], k: int) -> dict:
    adapter.setup()
    try:
        for memory in memories:
            adapter.insert(memory)

        case_results = []
        for case in cases:
            query = case["query"]
            retrieved = list(adapter.search(query, k))
            relevant = set(case.get("memorias_relevantes", []))
            trampa = set(case.get("memorias_trampa", []))

            case_results.append(
                {
                    "id": case["id"],
                    "categoria": case["categoria"],
                    "query": query,
                    "contexto_esperado": case.get("contexto_esperado"),
                    "memorias_relevantes": sorted(relevant),
                    "memorias_trampa": sorted(trampa),
                    "retrieved": retrieved,
                    "precision_at_k": precision_at_k(retrieved, relevant, k),
                    "recall_at_k": recall_at_k(retrieved, relevant, k),
                    "contaminated": is_contaminated(retrieved, trampa, k),
                }
            )
    finally:
        adapter.teardown()

    return {"k": k, "num_memories_indexed": len(memories), "cases": case_results}


def aggregate(case_results: list[dict]) -> dict:
    def summarize(subset: list[dict]) -> dict:
        precisions = [c["precision_at_k"] for c in subset]
        recalls = [c["recall_at_k"] for c in subset if c["recall_at_k"] is not None]
        contaminated = [1.0 if c["contaminated"] else 0.0 for c in subset]
        return {
            "n_casos": len(subset),
            "precision_at_k": round(mean(precisions), 4),
            "recall_at_k": round(mean(recalls), 4),
            "tasa_contaminacion": round(mean(contaminated), 4),
        }

    by_category: dict[str, dict] = {}
    for categoria in sorted({c["categoria"] for c in case_results}):
        subset = [c for c in case_results if c["categoria"] == categoria]
        by_category[categoria] = summarize(subset)

    return {
        "overall": summarize(case_results),
        "by_category": by_category,
    }


# ---------------------------------------------------------------------------
# Reporte en consola
# ---------------------------------------------------------------------------

def print_report(adapter_name: str, run_result: dict, agg: dict, include_superseded: bool) -> None:
    print()
    print("=" * 72)
    print(f"cerebro-memory retrieval eval - adaptador: {adapter_name}")
    print(
        f"k={run_result['k']}  memorias indexadas={run_result['num_memories_indexed']}"
        f"  incluye superseded={'si' if include_superseded else 'no'}"
        f"  casos={len(run_result['cases'])}"
    )
    print("=" * 72)

    header = f"{'Categoria':<12} {'Casos':>6} {'Precision@k':>13} {'Recall@k':>10} {'Contaminacion':>14}"
    print(header)
    print("-" * len(header))

    for categoria, stats in agg["by_category"].items():
        print(
            f"{categoria:<12} {stats['n_casos']:>6} {stats['precision_at_k']:>13.2%} "
            f"{stats['recall_at_k']:>10.2%} {stats['tasa_contaminacion']:>14.2%}"
        )

    print("-" * len(header))
    overall = agg["overall"]
    print(
        f"{'TOTAL':<12} {overall['n_casos']:>6} {overall['precision_at_k']:>13.2%} "
        f"{overall['recall_at_k']:>10.2%} {overall['tasa_contaminacion']:>14.2%}"
    )
    print()

    if not include_superseded:
        print(
            "Nota: la categoria 'temporal' solo es informativa con --include-superseded "
            "(sin esa flag las memorias superseded nunca se indexan, asi que no pueden "
            "contaminar el top-k y la contaminacion de 'temporal' sera trivialmente 0)."
        )
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Corre la suite de evaluación de retrieval de cerebro-memory.")
    parser.add_argument(
        "--adapter",
        required=True,
        choices=sorted(ADAPTERS.keys()),
        help="Adaptador de memoria a evaluar.",
    )
    parser.add_argument("--k", type=int, default=5, help="Top-k para precision/recall/contaminación (default: 5).")
    parser.add_argument(
        "--include-superseded",
        action="store_true",
        help="Inserta también las memorias con status 'superseded' (necesario para que la categoría 'temporal' sea significativa).",
    )
    parser.add_argument(
        "--memories",
        type=Path,
        default=EVALS_DIR / "memories.yaml",
        help="Ruta a memories.yaml (default: evals/memories.yaml).",
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=EVALS_DIR / "cases.yaml",
        help="Ruta a cases.yaml (default: evals/cases.yaml).",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=EVALS_DIR / "results",
        help="Directorio donde guardar el JSON de resultados (default: evals/results).",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="No escribe el JSON de resultados, solo imprime la tabla en consola.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    all_memories = load_memories(args.memories)
    memories = [m for m in all_memories if args.include_superseded or m.get("status") == "active"]
    cases = load_cases(args.cases)

    validate_case_ids(cases, {m["id"] for m in all_memories})

    adapter_cls = ADAPTERS[args.adapter]
    adapter = adapter_cls()

    run_result = run(adapter, memories, cases, args.k)
    agg = aggregate(run_result["cases"])

    print_report(args.adapter, run_result, agg, args.include_superseded)

    if not args.no_save:
        args.results_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        out_path = args.results_dir / f"{args.adapter}-{timestamp}.json"
        payload = {
            "adapter": args.adapter,
            "timestamp_utc": timestamp,
            "k": args.k,
            "include_superseded": args.include_superseded,
            "num_memories_total": len(all_memories),
            "num_memories_indexed": run_result["num_memories_indexed"],
            "num_cases": len(cases),
            "aggregates": agg,
            "cases": run_result["cases"],
        }
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"Resultados guardados en: {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
