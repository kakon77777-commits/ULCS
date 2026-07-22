from __future__ import annotations

from .executors import ExecutionError, get_adapter
from .model import GraphError, Program


def _input_type(node, by_id) -> str:
    if not node.inputs:
        return "None"
    if len(node.inputs) > 1:
        return "InputMap"
    return by_id[node.inputs[0].node_id].output_type


def enrich_and_validate(program: Program) -> Program:
    try:
        ordered = program.topological_nodes()
    except GraphError:
        raise

    by_id = program.node_map()
    for node in ordered:
        try:
            adapter = get_adapter(node.language)
        except ExecutionError as exc:
            raise TypeError(str(exc)) from exc

        input_type = _input_type(node, by_id)
        if input_type not in adapter.accepted_input_types and "Any" not in adapter.accepted_input_types:
            raise TypeError(
                f"節點 {node.node_id}：{node.language} 不接受輸入型別 {input_type}。"
            )
        node.input_type = input_type
        node.output_type = adapter.output_type
        node.effects = adapter.effects(node.code)
        node.runtime = adapter.runtime()
    return program


def plan_lines(program: Program) -> list[str]:
    lines: list[str] = []
    for index, node in enumerate(program.topological_nodes(), start=1):
        if not node.inputs:
            source = "無輸入"
        else:
            source = ", ".join(
                ref.node_id
                + (f".{ref.field}" if ref.field else "")
                + (f" as {ref.alias}" if ref.alias else "")
                for ref in node.inputs
            )
        effects = ", ".join(node.effects) if node.effects else "純計算／未偵測到外部副作用"
        lines.append(
            f"{index}. {node.node_id} [{node.language}] {node.input_type} → "
            f"{node.output_type}; 來源={source}; Runtime={node.runtime}; 副作用={effects}"
        )
    return lines
