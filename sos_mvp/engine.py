from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .executors import execute_node
from .model import Program


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    node_id: str
    value: Any


def execute_program(
    program: Program,
    *,
    cwd: Path,
    db_path: Path,
    timeout: int = 60,
    on_complete: Callable[[ExecutionEvent], None] | None = None,
) -> dict[str, Any]:
    """Execute a validated DAG in stable topological order.

    Independent branches are represented and scheduled correctly. v0.2 keeps
    execution deterministic and sequential; parallel layers can be added later
    without changing the LOG format.
    """
    outputs: dict[str, Any] = {}
    for node in program.topological_nodes():
        value = execute_node(node, outputs, cwd, db_path, timeout=timeout)
        outputs[node.node_id] = value
        if on_complete is not None:
            on_complete(ExecutionEvent(node.node_id, value))
    return outputs


def final_result(program: Program, outputs: dict[str, Any], output_node: str | None = None) -> Any:
    if output_node is not None:
        if output_node not in outputs:
            raise KeyError(f"指定的輸出節點不存在：{output_node}")
        return outputs[output_node]

    sinks = program.sink_nodes()
    if len(sinks) == 1:
        return outputs[sinks[0].node_id]
    return {node.node_id: outputs[node.node_id] for node in sinks}
