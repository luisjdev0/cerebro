"""Deterministic Mermaid rendering of a flow definition.

Generated from the *complete* definition, so it is for people (the portal, docs, a
review) and not part of the step-by-step execution protocol: a running agent must
still get one step at a time from `flow_start`/`flow_next`.

Shapes: `task` rectangle, `decision` diamond with its branches as edge labels,
`delegate` subroutine (steps sharing a `parallel_group` are boxed together),
`terminal` stadium, and the synthetic start (plus a prerequisites node when the
flow declares `tools`). A step with a required checkpoint is drawn with a thick
border, its normal edge is labelled "approved" and a dotted "rejected" edge goes
to its `on_reject` step.
"""

from __future__ import annotations

import re

from cerebro_flows.schema import FlowDefinitionSchema, Step

START_ID = "start__"
PREREQUISITES_ID = "prerequisites__"


def _node_id(step_id: str) -> str:
    # Mermaid ids: keep [A-Za-z0-9_]; the prefix avoids clashes with reserved words ("end", "graph"...).
    return "s_" + re.sub(r"[^A-Za-z0-9_]", "_", step_id)


def _text(value: str) -> str:
    """Label text safe inside a double-quoted Mermaid string."""
    return value.replace("\\", "/").replace('"', "#quot;").replace("\n", " ").strip()


def _edge_label(value: str) -> str:
    return _text(value).replace("|", "#124;")


def _shape(step: Step) -> str:
    nid = _node_id(step.id)
    label = f"<b>{_text(step.id)}</b>"
    if step.description:
        short = step.description.strip().splitlines()[0]
        label += "<br/>" + _text(short if len(short) <= 60 else short[:57] + "...")
    if step.checkpoint is not None and step.checkpoint.required:
        label += "<br/>(approval required)"
    if step.terminal:
        return f'{nid}(["{label}"])'
    if step.type == "decision":
        return f'{nid}{{"{label}"}}'
    if step.type == "delegate":
        return f'{nid}[["{label}"]]'
    return f'{nid}["{label}"]'


def flow_to_mermaid(flow: FlowDefinitionSchema) -> str:
    lines = ["flowchart TD"]
    indent = "    "

    lines.append(f'{indent}{START_ID}(("start"))')
    if flow.tools:
        tools = ", ".join(_text(t) for t in flow.tools)
        lines.append(f'{indent}{PREREQUISITES_ID}["<b>prerequisites</b><br/>tools: {tools}"]')
        lines.append(f"{indent}{START_ID} --> {PREREQUISITES_ID}")
        lines.append(f"{indent}{PREREQUISITES_ID} --> {_node_id(flow.entry)}")
    else:
        lines.append(f"{indent}{START_ID} --> {_node_id(flow.entry)}")

    groups: dict[str, list[Step]] = {}
    for step in flow.procedure:
        group = step.delegate.parallel_group if step.delegate is not None else None
        if group is None:
            lines.append(f"{indent}{_shape(step)}")
        else:
            groups.setdefault(group, []).append(step)
    for group, steps in groups.items():
        gid = "pg_" + re.sub(r"[^A-Za-z0-9_]", "_", group)
        lines.append(f'{indent}subgraph {gid}["parallel: {_text(group)}"]')
        for step in steps:
            lines.append(f"{indent}{indent}{_shape(step)}")
        lines.append(f"{indent}end")

    for step in flow.procedure:
        src = _node_id(step.id)
        gated = step.checkpoint is not None and step.checkpoint.required
        if step.branches:
            for condition, target in step.branches.items():
                lines.append(f"{indent}{src} -->|{_edge_label(condition)}| {_node_id(target)}")
        elif step.next is not None:
            arrow = f"{src} -->|approved| " if gated else f"{src} --> "
            lines.append(f"{indent}{arrow}{_node_id(step.next)}")
        if step.checkpoint is not None:
            lines.append(f"{indent}{src} -.->|rejected| {_node_id(step.checkpoint.on_reject)}")

    gated_ids = [_node_id(s.id) for s in flow.procedure if s.checkpoint is not None and s.checkpoint.required]
    if gated_ids:
        lines.append(f"{indent}classDef checkpoint stroke-width:3px")
        lines.append(f"{indent}class {','.join(gated_ids)} checkpoint")
    return "\n".join(lines) + "\n"


def flow_references(flow: FlowDefinitionSchema) -> list[dict[str, str | None]]:
    """Documents the definition points at: `metadata.references` and each delegate step's `prompt_ref`.
    Not resolved here -- they are `cerebro-docs://category/slug` URIs for whoever consumes them."""
    refs: list[dict[str, str | None]] = [{"ref": r, "kind": "metadata", "step_id": None} for r in flow.metadata.references]
    for step in flow.procedure:
        if step.delegate is not None and step.delegate.prompt_ref:
            refs.append({"ref": step.delegate.prompt_ref, "kind": "prompt", "step_id": step.id})
    return refs
