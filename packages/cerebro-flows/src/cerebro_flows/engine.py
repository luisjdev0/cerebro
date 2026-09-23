"""Motor de ejecucion "semaforo" (luisjdev-pendientes/cerebro-flows SS3-4). El modelo
nunca recibe el YAML completo -- cada funcion de aqui revela solo el paso que toca.

Redis (`flow_run:<run_id>`) guarda SOLO el puntero mutable de donde esta la
ejecucion; la definicion se relee y reparsea de Postgres en cada paso (nunca se
cachea el procedure completo en Redis, ver SS5 del documento de diseno). Postgres
(`flow_run_events`) es el rastro de auditoria, escrito incrementalmente en cada
transicion real -- sobrevive aunque el run se abandone y expire por TTL en Redis.

El primer "paso" que devuelve `start_run` es sintetico (`id="__prerequisites__"`),
generado de la lista `tools:` del YAML -- el chequeo en si lo hace el modelo
(intentar ToolSearch, avisar si de verdad falta algo); el servidor solo lo obliga a
aparecer primero en el protocolo (SS4).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import asyncpg
import redis.asyncio as redis

from cerebro_flows.config import Settings
from cerebro_flows.schema import FlowDefinitionSchema, Step, validate_flow_yaml

PREREQUISITES_STEP_ID = "__prerequisites__"


class FlowNotFoundError(LookupError):
    """No existe ninguna definicion de flujo activa con ese `code`."""


class RunNotFoundError(LookupError):
    """El `run_id` no existe, o su puntero en Redis expiro por inactividad (el
    mensaje distingue ambos casos consultando `flow_runs` en Postgres)."""


class RunAlreadyFinishedError(ValueError):
    """El run ya esta `completed`/`aborted` -- no admite mas transiciones."""


class InvalidDecisionError(ValueError):
    """El step actual es `decision` y el `decision` reportado no es una de sus
    `branches` validas."""


class CheckpointPendingError(ValueError):
    """El step actual tiene un checkpoint sin aprobar/rechazar -- `flow_next` no
    puede avanzar hasta `approve_checkpoint`/`reject_checkpoint`."""


class NotAtCheckpointError(ValueError):
    """`approve_checkpoint`/`reject_checkpoint` llamado sobre un step sin checkpoint
    (o ya resuelto)."""


def _redis_key(run_id: str) -> str:
    return f"flow_run:{run_id}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _load_pointer(redis_client: redis.Redis, run_id: str) -> dict[str, Any] | None:
    raw = await redis_client.get(_redis_key(run_id))
    return json.loads(raw) if raw else None


async def _save_pointer(redis_client: redis.Redis, settings: Settings, run_id: str, pointer: dict[str, Any]) -> None:
    pointer["last_activity_at"] = _now()
    await redis_client.set(
        _redis_key(run_id), json.dumps(pointer), ex=settings.flow_run_ttl_hours * 3600
    )


async def _load_definition(pool: asyncpg.Pool, definition_id: str, version: int) -> FlowDefinitionSchema:
    row = await pool.fetchrow(
        "SELECT yaml_content FROM flow_definition_versions WHERE definition_id = $1 AND version_number = $2",
        UUID(definition_id),
        version,
    )
    if row is None:
        raise FlowNotFoundError(f"definition {definition_id} version {version} not found")
    return validate_flow_yaml(row["yaml_content"])


async def _log_event(
    pool: asyncpg.Pool, run_id: str, event_type: str, step_id: str | None, payload: dict[str, Any] | None = None
) -> None:
    await pool.execute(
        "INSERT INTO flow_run_events (run_id, event_type, step_id, payload) VALUES ($1, $2, $3, $4)",
        UUID(run_id),
        event_type,
        step_id,
        json.dumps(payload) if payload is not None else None,
    )


def _prerequisites_step(tools: list[str]) -> dict[str, Any]:
    return {
        "id": PREREQUISITES_STEP_ID,
        "type": "prerequisites",
        "description": (
            "Antes de continuar, confirma que tienes disponibles todas las tools listadas en "
            "'tools_required'. Si alguna esta diferida en tu entorno, intenta ToolSearch antes de "
            "darla por ausente. Si de verdad falta alguna, informa al usuario y no continues -- "
            "llama flow_next solo cuando las tengas todas."
        ),
        "tools_required": tools,
    }


def _step_payload(step: Step) -> dict[str, Any]:
    return step.model_dump(exclude_none=True)


async def start_run(
    pool: asyncpg.Pool, redis_client: redis.Redis, settings: Settings, flow_code: str
) -> dict[str, Any]:
    def_row = await pool.fetchrow(
        "SELECT id, current_version FROM flow_definitions WHERE code = $1", flow_code
    )
    if def_row is None:
        raise FlowNotFoundError(flow_code)

    definition_id = str(def_row["id"])
    version = def_row["current_version"]
    parsed = await _load_definition(pool, definition_id, version)

    run_row = await pool.fetchrow(
        """
        INSERT INTO flow_runs (definition_id, definition_version)
        VALUES ($1, $2) RETURNING run_id
        """,
        def_row["id"],
        version,
    )
    run_id = str(run_row["run_id"])

    pointer = {
        "flow_code": flow_code,
        "definition_id": definition_id,
        "definition_version": version,
        "current_step_id": None,
        "status": "in_progress",
        "pending_checkpoint": None,
        "started_at": _now(),
    }

    if parsed.tools:
        pointer["current_step_id"] = PREREQUISITES_STEP_ID
        await _save_pointer(redis_client, settings, run_id, pointer)
        await _log_event(pool, run_id, "step_entered", PREREQUISITES_STEP_ID)
        return {"run_id": run_id, "status": "in_progress", "step": _prerequisites_step(parsed.tools)}

    # Sin prerequisitos: entra directo al `entry` -- reusa `_enter_step` para que un
    # entry que ya es terminal (flujo de un solo paso) se marque `completed` igual
    # que cualquier otra transicion hacia un step terminal, en vez de quedar
    # `in_progress` colgado con un step que nunca se va a poder "avanzar".
    return await _enter_step(pool, redis_client, settings, run_id, pointer, parsed, parsed.entry, "step_entered")


async def _require_pointer(pool: asyncpg.Pool, redis_client: redis.Redis, run_id: str) -> dict[str, Any]:
    pointer = await _load_pointer(redis_client, run_id)
    if pointer is not None:
        return pointer

    row = await pool.fetchrow("SELECT status FROM flow_runs WHERE run_id = $1", UUID(run_id))
    if row is None:
        raise RunNotFoundError(f"run_id '{run_id}' no existe")
    if row["status"] in ("completed", "aborted"):
        raise RunAlreadyFinishedError(f"run_id '{run_id}' ya esta '{row['status']}'")
    raise RunNotFoundError(
        f"run_id '{run_id}' expiro por inactividad (TTL) sin completarse -- considera flow_start de nuevo"
    )


async def advance(
    pool: asyncpg.Pool,
    redis_client: redis.Redis,
    settings: Settings,
    run_id: str,
    *,
    decision: str | None = None,
) -> dict[str, Any]:
    pointer = await _require_pointer(pool, redis_client, run_id)

    if pointer["current_step_id"] == PREREQUISITES_STEP_ID:
        parsed = await _load_definition(pool, pointer["definition_id"], pointer["definition_version"])
        return await _enter_step(pool, redis_client, settings, run_id, pointer, parsed, parsed.entry, "step_entered")

    parsed = await _load_definition(pool, pointer["definition_id"], pointer["definition_version"])
    current = parsed.step(pointer["current_step_id"])

    pending = pointer.get("pending_checkpoint")
    if current.checkpoint is not None and current.checkpoint.required and not (pending and pending.get("approved")):
        raise CheckpointPendingError(
            f"step '{current.id}' tiene un checkpoint pendiente -- llama flow_approve_checkpoint "
            "o flow_reject_checkpoint antes de flow_next"
        )

    if current.type == "decision":
        if decision is None or decision not in (current.branches or {}):
            valid = sorted((current.branches or {}).keys())
            raise InvalidDecisionError(
                f"step '{current.id}' es una decision -- pasa 'decision' con uno de: {valid}"
            )
        next_id = current.branches[decision]
        event_type = "decision_taken"
        event_payload = {"decision": decision}
    else:
        next_id = current.next
        event_type = "step_entered"
        event_payload = None

    return await _enter_step(pool, redis_client, settings, run_id, pointer, parsed, next_id, event_type, event_payload)


async def _enter_step(
    pool: asyncpg.Pool,
    redis_client: redis.Redis,
    settings: Settings,
    run_id: str,
    pointer: dict[str, Any],
    parsed: FlowDefinitionSchema,
    next_id: str,
    event_type: str,
    event_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    next_step = parsed.step(next_id)

    if next_step.terminal:
        await pool.execute(
            "UPDATE flow_runs SET status = 'completed', completed_at = now() WHERE run_id = $1",
            UUID(run_id),
        )
        await _log_event(pool, run_id, event_type, next_id, event_payload)
        await _log_event(pool, run_id, "completed", next_id)
        await redis_client.delete(_redis_key(run_id))
        return {"run_id": run_id, "status": "completed", "step": None}

    steps_payload: Any
    if next_step.delegate is not None and next_step.delegate.parallel_group is not None:
        group = next_step.delegate.parallel_group
        siblings = [
            s for s in parsed.procedure if s.delegate is not None and s.delegate.parallel_group == group
        ]
        steps_payload = [_step_payload(s) for s in siblings]
    else:
        steps_payload = _step_payload(next_step)

    pointer["current_step_id"] = next_id
    pointer["pending_checkpoint"] = None
    await _save_pointer(redis_client, settings, run_id, pointer)
    await _log_event(pool, run_id, event_type, next_id, event_payload)

    return {"run_id": run_id, "status": "in_progress", "step": steps_payload}


async def approve_checkpoint(pool: asyncpg.Pool, redis_client: redis.Redis, settings: Settings, run_id: str) -> dict[str, Any]:
    pointer = await _require_pointer(pool, redis_client, run_id)
    parsed = await _load_definition(pool, pointer["definition_id"], pointer["definition_version"])
    current = parsed.step(pointer["current_step_id"])
    if current.checkpoint is None:
        raise NotAtCheckpointError(f"step '{current.id}' no tiene checkpoint")

    pointer["pending_checkpoint"] = {"step_id": current.id, "approved": True}
    await _save_pointer(redis_client, settings, run_id, pointer)
    await _log_event(pool, run_id, "checkpoint_approved", current.id)

    return {"run_id": run_id, "status": "in_progress", "step": _step_payload(current)}


async def reject_checkpoint(
    pool: asyncpg.Pool, redis_client: redis.Redis, settings: Settings, run_id: str, reason: str
) -> dict[str, Any]:
    pointer = await _require_pointer(pool, redis_client, run_id)
    parsed = await _load_definition(pool, pointer["definition_id"], pointer["definition_version"])
    current = parsed.step(pointer["current_step_id"])
    if current.checkpoint is None:
        raise NotAtCheckpointError(f"step '{current.id}' no tiene checkpoint")

    await _log_event(pool, run_id, "checkpoint_rejected", current.id, {"reason": reason})
    return await _enter_step(
        pool, redis_client, settings, run_id, pointer, parsed, current.checkpoint.on_reject, "step_entered"
    )


async def abort_run(pool: asyncpg.Pool, redis_client: redis.Redis, run_id: str, reason: str | None = None) -> dict[str, Any]:
    row = await pool.fetchrow(
        """
        UPDATE flow_runs SET status = 'aborted', completed_at = now()
        WHERE run_id = $1 AND status = 'in_progress'
        RETURNING run_id
        """,
        UUID(run_id),
    )
    if row is None:
        existing = await pool.fetchrow("SELECT status FROM flow_runs WHERE run_id = $1", UUID(run_id))
        if existing is None:
            raise RunNotFoundError(f"run_id '{run_id}' no existe")
        raise RunAlreadyFinishedError(f"run_id '{run_id}' ya esta '{existing['status']}'")

    await _log_event(pool, run_id, "aborted", None, {"reason": reason} if reason else None)
    await redis_client.delete(_redis_key(run_id))
    return {"run_id": run_id, "status": "aborted"}


async def get_run_state(pool: asyncpg.Pool, redis_client: redis.Redis, run_id: str) -> dict[str, Any]:
    pointer = await _load_pointer(redis_client, run_id)
    if pointer is not None:
        parsed = await _load_definition(pool, pointer["definition_id"], pointer["definition_version"])
        step_id = pointer["current_step_id"]
        step_payload = (
            _prerequisites_step(parsed.tools) if step_id == PREREQUISITES_STEP_ID else _step_payload(parsed.step(step_id))
        )
        return {"run_id": run_id, "status": "in_progress", "step": step_payload}

    row = await pool.fetchrow("SELECT status FROM flow_runs WHERE run_id = $1", UUID(run_id))
    if row is None:
        raise RunNotFoundError(f"run_id '{run_id}' no existe")
    return {"run_id": run_id, "status": row["status"], "step": None}
