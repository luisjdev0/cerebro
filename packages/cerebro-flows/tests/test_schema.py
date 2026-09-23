"""Unit tests (sin DB) de `cerebro_flows.schema`: parseo y validacion referencial del
YAML de un flujo (luisjdev-pendientes/cerebro-flows SS2)."""

from __future__ import annotations

import pytest

from cerebro_flows.schema import InvalidFlowSchemaError, validate_flow_yaml

MINIMAL = """
metadata:
  name: Flujo minimo
  category: test
entry: only
procedure:
  - id: only
    type: task
    terminal: true
"""

# El flujo de ejemplo INC-22 del documento de diseno (luisjdev-pendientes/cerebro-flows SS2).
INC_22 = """
metadata:
  name: Procesamiento de incidencia
  category: incident
  description: Analizar y resolver incidencias reportadas por usuarios.
  author: jose
  references:
    - cerebro-docs://refs/incident-audit-prompt

input:
  required:
    - name: incident
      type: string
      description: Referencia de la incidencia reportada.
  optional:
    - name: attachments
      type: array
      description: Adjuntos opcionales.

output:
  type: object
  properties:
    resolution: string

entry: analyze

procedure:
  - id: analyze
    type: task
    description: Analizar la incidencia.
    next: investigate

  - id: investigate
    type: task
    description: Investigar posibles causas.
    next: decision

  - id: decision
    type: decision
    description: Determinar si existe informacion suficiente.
    branches:
      sufficient: resolve
      insufficient: request_information

  - id: request_information
    type: task
    description: Solicitar la informacion faltante.
    next: investigate

  - id: resolve
    type: task
    description: Resolver la incidencia.
    checkpoint:
      required: true
      prompt: "Confirmas que se cree el ticket de resolucion?"
      on_reject: analyze
    tools_allowed: [create_ticket]
    next: end

  - id: end
    type: task
    description: Cierre del flujo.
    terminal: true

rules:
  - id: R001
    rule: No asumir informacion que no este presente en el contexto.
  - id: R002
    rule: Si existen varias hipotesis, mantenerlas separadas hasta tener evidencia suficiente.
    applies_to: [investigate, decision]

tools:
  - search_incidents
  - read_file
  - create_ticket

execution_policy:
  autonomy: guided
  confirmation_required:
    - create_ticket
"""


def test_minimal_flow_is_valid():
    parsed = validate_flow_yaml(MINIMAL)
    assert parsed.entry == "only"
    assert parsed.step("only").terminal


def test_inc_22_example_from_design_doc_is_valid():
    parsed = validate_flow_yaml(INC_22)
    assert [s.id for s in parsed.procedure] == [
        "analyze", "investigate", "decision", "request_information", "resolve", "end",
    ]
    assert parsed.step("decision").branches == {"sufficient": "resolve", "insufficient": "request_information"}
    assert parsed.step("resolve").checkpoint.on_reject == "analyze"
    assert parsed.tools == ["search_incidents", "read_file", "create_ticket"]
    assert parsed.execution_policy.confirmation_required == ["create_ticket"]


def test_malformed_yaml_is_invalid_flow_schema_error():
    with pytest.raises(InvalidFlowSchemaError, match="YAML invalido"):
        validate_flow_yaml("metadata: [unclosed")


def test_non_mapping_top_level_is_rejected():
    with pytest.raises(InvalidFlowSchemaError, match="mapeo de nivel superior"):
        validate_flow_yaml("- a\n- b\n")


def test_unknown_field_is_rejected():
    with pytest.raises(InvalidFlowSchemaError):
        validate_flow_yaml(MINIMAL.replace("entry: only", "entry: only\nnonexistent_field: x"))


def test_entry_must_reference_existing_step():
    bad = MINIMAL.replace("entry: only", "entry: missing")
    with pytest.raises(InvalidFlowSchemaError, match="no existe en 'procedure'"):
        validate_flow_yaml(bad)


def test_duplicate_step_id_is_rejected():
    bad = """
metadata: {name: x, category: y}
entry: a
procedure:
  - id: a
    type: task
    next: a
  - id: a
    type: task
    terminal: true
"""
    with pytest.raises(InvalidFlowSchemaError, match="duplicado"):
        validate_flow_yaml(bad)


def test_task_without_next_or_terminal_is_rejected():
    bad = """
metadata: {name: x, category: y}
entry: a
procedure:
  - id: a
    type: task
"""
    with pytest.raises(InvalidFlowSchemaError, match="falta 'next'"):
        validate_flow_yaml(bad)


def test_decision_without_branches_is_rejected():
    bad = """
metadata: {name: x, category: y}
entry: a
procedure:
  - id: a
    type: decision
    next: a
"""
    with pytest.raises(InvalidFlowSchemaError, match="requiere 'branches'"):
        validate_flow_yaml(bad)


def test_terminal_with_next_is_rejected():
    bad = """
metadata: {name: x, category: y}
entry: a
procedure:
  - id: a
    type: task
    terminal: true
    next: a
"""
    with pytest.raises(InvalidFlowSchemaError, match="no puede tener"):
        validate_flow_yaml(bad)


def test_next_pointing_to_missing_step_is_rejected():
    bad = """
metadata: {name: x, category: y}
entry: a
procedure:
  - id: a
    type: task
    next: nowhere
  - id: b
    type: task
    terminal: true
"""
    with pytest.raises(InvalidFlowSchemaError, match="next='nowhere' no existe"):
        validate_flow_yaml(bad)


def test_branches_pointing_to_missing_step_is_rejected():
    bad = """
metadata: {name: x, category: y}
entry: a
procedure:
  - id: a
    type: decision
    branches:
      yes: nowhere
  - id: b
    type: task
    terminal: true
"""
    with pytest.raises(InvalidFlowSchemaError, match="branches.yes='nowhere' no existe"):
        validate_flow_yaml(bad)


def test_checkpoint_on_reject_pointing_to_missing_step_is_rejected():
    bad = """
metadata: {name: x, category: y}
entry: a
procedure:
  - id: a
    type: task
    terminal: true
    checkpoint:
      prompt: confirmas?
      on_reject: nowhere
"""
    with pytest.raises(InvalidFlowSchemaError, match="checkpoint.on_reject='nowhere' no existe"):
        validate_flow_yaml(bad)


def test_rule_applies_to_missing_step_is_rejected():
    bad = """
metadata: {name: x, category: y}
entry: a
procedure:
  - id: a
    type: task
    terminal: true
rules:
  - id: R1
    rule: algo
    applies_to: [nowhere]
"""
    with pytest.raises(InvalidFlowSchemaError, match="applies_to referencia el step inexistente"):
        validate_flow_yaml(bad)


def test_no_terminal_step_is_rejected():
    bad = """
metadata: {name: x, category: y}
entry: a
procedure:
  - id: a
    type: task
    next: a
"""
    with pytest.raises(InvalidFlowSchemaError, match="ningun step con terminal"):
        validate_flow_yaml(bad)


def test_delegate_requires_delegate_block():
    bad = """
metadata: {name: x, category: y}
entry: a
procedure:
  - id: a
    type: delegate
    next: b
  - id: b
    type: task
    terminal: true
"""
    with pytest.raises(InvalidFlowSchemaError, match="requiere el bloque 'delegate'"):
        validate_flow_yaml(bad)


def test_parallel_group_siblings_must_share_next():
    bad = """
metadata: {name: x, category: y}
entry: a
procedure:
  - id: a
    type: task
    next: p1
  - id: p1
    type: delegate
    delegate: {agent_type: reviewer, parallel_group: g1}
    next: end1
  - id: p2
    type: delegate
    delegate: {agent_type: reviewer, parallel_group: g1}
    next: end2
  - id: end1
    type: task
    terminal: true
  - id: end2
    type: task
    terminal: true
"""
    with pytest.raises(InvalidFlowSchemaError, match="parallel_group 'g1'"):
        validate_flow_yaml(bad)


def test_branches_named_yes_no_are_not_coerced_to_booleans():
    """El 'Norway problem' de YAML 1.1: 'yes'/'no' sin comillas se interpretan como
    booleanos por defecto. Una decision si/no es el caso mas natural del mundo para
    nombrar sus branches asi -- deben sobrevivir como los strings literales "yes"/"no"."""
    ok = """
metadata: {name: x, category: y}
entry: a
procedure:
  - id: a
    type: decision
    branches:
      yes: b
      no: c
  - id: b
    type: task
    terminal: true
  - id: c
    type: task
    terminal: true
"""
    parsed = validate_flow_yaml(ok)
    assert parsed.step("a").branches == {"yes": "b", "no": "c"}


def test_true_false_still_parse_as_real_booleans():
    ok = """
metadata: {name: x, category: y}
entry: a
procedure:
  - id: a
    type: task
    terminal: true
    checkpoint:
      required: true
      prompt: confirmas?
      on_reject: a
"""
    parsed = validate_flow_yaml(ok)
    assert parsed.step("a").checkpoint.required is True


def test_parallel_group_siblings_with_same_next_is_valid():
    ok = """
metadata: {name: x, category: y}
entry: a
procedure:
  - id: a
    type: task
    next: p1
  - id: p1
    type: delegate
    delegate: {agent_type: reviewer, parallel_group: g1}
    next: join
  - id: p2
    type: delegate
    delegate: {agent_type: auditor, parallel_group: g1}
    next: join
  - id: join
    type: task
    terminal: true
"""
    parsed = validate_flow_yaml(ok)
    assert parsed.step("p1").delegate.parallel_group == "g1"
