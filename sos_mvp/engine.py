from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .capabilities import CapabilityPolicy
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
    policy: CapabilityPolicy | None = None,
    on_complete: Callable[[ExecutionEvent], None] | None = None,
) -> dict[str, Any]:
    """Execute a validated DAG in stable topological order.

    When a capability policy is supplied, the complete graph is checked before
    any Runtime starts. This prevents partially executed workflows when a later
    node lacks authorization.
    """
    if policy is not None:
        policy.check_program(program)

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
