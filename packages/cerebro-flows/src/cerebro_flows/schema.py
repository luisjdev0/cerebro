"""Parseo y validacion estructural del YAML de un flujo
(luisjdev-pendientes/cerebro-flows SS2). El YAML es la fuente de verdad del
`procedure` (el grafo de pasos); el id correlativo humano ("INC-22") y el numero de
version son asignados por el servidor al guardar (`flow_definitions.code`/
`flow_definition_versions.version_number`), NO se leen de aqui -- por eso este
schema no incluye `id`/`version` de nivel raiz, solo la estructura del flujo en si.

Error explicito, nunca adivinar (mismo criterio que `cerebro_docs.sections`): un
`next`/`branches`/`checkpoint.on_reject` que apunte a un step inexistente, un step
`decision` sin `branches`, un step no-terminal sin `next`, etc. son todos
`InvalidFlowSchemaError` con el step/campo exacto en el mensaje.
"""

from __future__ import annotations

import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

StepType = Literal["task", "decision", "delegate"]


class _FlowYamlLoader(yaml.SafeLoader):
    """`yaml.safe_load` normal, salvo que NO interpreta `yes`/`no`/`on`/`off` como
    booleanos (el "Norway problem" de YAML 1.1) -- una branch de decision llamada
    "yes"/"no" (el caso mas natural para una decision de si/no) debe quedar como el
    string literal, no colapsar a `True`/`False`. `true`/`false` SI se resuelven a
    booleanos como siempre (los usamos a proposito en `terminal`/`checkpoint.required`);
    ademas pydantic coacciona strings como "true"/"false" a bool igual si hiciera
    falta, asi que no se pierde nada quitando solo yes/no/on/off."""


_FlowYamlLoader.yaml_implicit_resolvers = {
    key: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_FlowYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


class InvalidFlowSchemaError(ValueError):
    """El YAML no es valido: ni parseable, ni pydantic-valido, ni referencialmente
    consistente (ver `validate_flow_yaml`). El mensaje siempre es puntual."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InputField(StrictModel):
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    description: str = ""


class IOSpec(StrictModel):
    required: list[InputField] = Field(default_factory=list)
    optional: list[InputField] = Field(default_factory=list)


class OutputSpec(StrictModel):
    type: str = "object"
    properties: dict[str, str] = Field(default_factory=dict)


class Metadata(StrictModel):
    name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    description: str = ""
    author: str | None = None
    status: str = "active"
    created_at: str | None = None
    updated_at: str | None = None
    # URIs a cerebro-docs (p.ej. "cerebro-docs://categoria/slug") resueltas por quien
    # consume el paso, no por el motor -- ver SS9 del documento de diseno.
    references: list[str] = Field(default_factory=list)


class Checkpoint(StrictModel):
    required: bool = True
    prompt: str = Field(min_length=1)
    on_reject: str = Field(min_length=1)


class Delegate(StrictModel):
    agent_type: str = Field(min_length=1)
    prompt_ref: str | None = None
    parallel_group: str | None = None


class Step(StrictModel):
    id: str = Field(min_length=1)
    type: StepType
    description: str = ""
    next: str | None = None
    branches: dict[str, str] | None = None
    terminal: bool = False
    checkpoint: Checkpoint | None = None
    tools_allowed: list[str] | None = None
    delegate: Delegate | None = None

    @model_validator(mode="after")
    def _shape_matches_type(self) -> "Step":
        if self.type == "delegate" and self.delegate is None:
            raise ValueError(f"step '{self.id}': type='delegate' requiere el bloque 'delegate'")
        if self.type != "delegate" and self.delegate is not None:
            raise ValueError(f"step '{self.id}': 'delegate' solo aplica a type='delegate'")
        if self.type == "decision" and self.branches is None:
            raise ValueError(f"step '{self.id}': type='decision' requiere 'branches'")
        if self.type != "decision" and self.branches is not None:
            raise ValueError(f"step '{self.id}': 'branches' solo aplica a type='decision'")
        if self.terminal:
            if self.next is not None or self.branches is not None:
                raise ValueError(f"step '{self.id}': terminal=true no puede tener 'next' ni 'branches'")
        else:
            if self.type == "decision":
                if not self.branches:
                    raise ValueError(f"step '{self.id}': 'branches' no puede estar vacio")
            elif self.next is None:
                raise ValueError(
                    f"step '{self.id}': falta 'next' (obligatorio salvo terminal=true o type='decision')"
                )
        return self


class Rule(StrictModel):
    id: str = Field(min_length=1)
    rule: str = Field(min_length=1)
    applies_to: list[str] = Field(default_factory=list)


class ExecutionPolicy(StrictModel):
    autonomy: str = "guided"
    confirmation_required: list[str] = Field(default_factory=list)


class FlowDefinitionSchema(StrictModel):
    metadata: Metadata
    input: IOSpec = Field(default_factory=IOSpec)
    output: OutputSpec = Field(default_factory=OutputSpec)
    entry: str = Field(min_length=1)
    procedure: list[Step] = Field(min_length=1)
    rules: list[Rule] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)

    @model_validator(mode="after")
    def _referential_integrity(self) -> "FlowDefinitionSchema":
        step_ids = [s.id for s in self.procedure]
        seen: set[str] = set()
        for step_id in step_ids:
            if step_id in seen:
                raise ValueError(f"step id duplicado: '{step_id}'")
            seen.add(step_id)
        step_by_id = {s.id: s for s in self.procedure}

        if self.entry not in step_by_id:
            raise ValueError(f"'entry: {self.entry}' no existe en 'procedure'")

        def _check_target(source_step: str, field: str, target: str) -> None:
            if target not in step_by_id:
                raise ValueError(f"step '{source_step}': {field}='{target}' no existe en 'procedure'")

        parallel_groups: dict[str, set[str]] = {}
        for step in self.procedure:
            if step.next is not None:
                _check_target(step.id, "next", step.next)
            if step.branches is not None:
                for condition, target in step.branches.items():
                    _check_target(step.id, f"branches.{condition}", target)
            if step.checkpoint is not None:
                _check_target(step.id, "checkpoint.on_reject", step.checkpoint.on_reject)
            if step.delegate is not None and step.delegate.parallel_group is not None:
                parallel_groups.setdefault(step.delegate.parallel_group, set()).add(step.next or "")

        for group, next_targets in parallel_groups.items():
            if len(next_targets) > 1:
                raise ValueError(
                    f"parallel_group '{group}': todos sus steps deben compartir el mismo 'next' (encontrados: {sorted(next_targets)})"
                )

        for rule in self.rules:
            for step_id in rule.applies_to:
                if step_id not in step_by_id:
                    raise ValueError(f"rule '{rule.id}': applies_to referencia el step inexistente '{step_id}'")

        if not any(s.terminal for s in self.procedure):
            raise ValueError("el flujo no tiene ningun step con terminal=true")

        return self

    def step(self, step_id: str) -> Step:
        for s in self.procedure:
            if s.id == step_id:
                return s
        raise KeyError(step_id)


def validate_flow_yaml(text: str) -> FlowDefinitionSchema:
    """Parsea y valida un YAML de flujo completo. Nunca adivina: cualquier problema
    (YAML mal formado, campo faltante/desconocido, referencia rota) es
    `InvalidFlowSchemaError` con el detalle puntual."""
    try:
        raw: Any = yaml.load(text, Loader=_FlowYamlLoader)
    except yaml.YAMLError as exc:
        raise InvalidFlowSchemaError(f"YAML invalido: {exc}") from exc

    if not isinstance(raw, dict):
        raise InvalidFlowSchemaError("el YAML debe ser un mapeo de nivel superior (metadata, procedure, ...)")

    try:
        return FlowDefinitionSchema.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise InvalidFlowSchemaError(f"{loc}: {first['msg']}" if loc else first["msg"]) from exc
