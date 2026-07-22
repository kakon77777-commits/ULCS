from __future__ import annotations

import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .capabilities import CapabilityPolicy
from .executors import execute_node
from .model import Node, Program
from .resources import ExecutionLimits, ResourceLimitError


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    node_id: str
    value: Any
    taints: tuple[str, ...] = ()
    output_bytes: int = 0
    layer: int = 0


@dataclass(frozen=True, slots=True)
class ExecutionTrace:
    outputs: dict[str, Any]
    taints: dict[str, tuple[str, ...]]
    output_bytes: dict[str, int]
    total_output_bytes: int
    execution_layers: tuple[tuple[str, ...], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "outputs": self.outputs,
            "taints": {key: list(value) for key, value in self.taints.items()},
            "output_bytes": dict(self.output_bytes),
            "total_output_bytes": self.total_output_bytes,
            "execution_layers": [list(layer) for layer in self.execution_layers],
        }


def execute_program(
    program: Program,
    *,
    cwd: Path,
    db_path: Path,
    timeout: int = 60,
    policy: CapabilityPolicy | None = None,
    limits: ExecutionLimits | None = None,
    on_complete: Callable[[ExecutionEvent], None] | None = None,
) -> dict[str, Any]:
    return execute_program_with_trace(
        program,
        cwd=cwd,
        db_path=db_path,
        timeout=timeout,
        policy=policy,
        limits=limits,
        on_complete=on_complete,
    ).outputs


def execute_program_with_trace(
    program: Program,
    *,
    cwd: Path,
    db_path: Path,
    timeout: int = 60,
    policy: CapabilityPolicy | None = None,
    limits: ExecutionLimits | None = None,
    on_complete: Callable[[ExecutionEvent], None] | None = None,
) -> ExecutionTrace:
    """Execute a validated DAG layer-by-layer with bounded parallelism.

    Capability authorization and node-count quotas are checked before any
    Runtime starts. Nodes within one dependency layer may run concurrently.
    Mutating filesystem/database nodes share a conservative serialization lock.
    """
    if policy is not None:
        policy.check_program(program)
    effective_limits = limits or (policy.limits if policy is not None else ExecutionLimits())

    if len(program.nodes) > effective_limits.max_nodes:
        raise ResourceLimitError(
            f"節點數 {len(program.nodes)} 超過上限 {effective_limits.max_nodes}。"
        )

    outputs: dict[str, Any] = {}
    taints: dict[str, tuple[str, ...]] = {}
    output_bytes: dict[str, int] = {}
    total_output_bytes = 0
    serial_effect_lock = threading.Lock()
    layers = program.execution_layers()

    for layer_number, layer in enumerate(layers, start=1):
        worker_count = min(effective_limits.max_workers, len(layer))
        futures: dict[str, Future[Any]] = {}

        def run(node: Node) -> Any:
            if _requires_serial_effects(node):
                with serial_effect_lock:
                    return execute_node(node, outputs, cwd, db_path, timeout=timeout)
            return execute_node(node, outputs, cwd, db_path, timeout=timeout)

        with ThreadPoolExecutor(
            max_workers=max(1, worker_count),
            thread_name_prefix=f"ulcs-layer-{layer_number}",
        ) as executor:
            for node in layer:
                futures[node.node_id] = executor.submit(run, node)

            for node in layer:
                try:
                    value = futures[node.node_id].result()
                except Exception:
                    for future in futures.values():
                        future.cancel()
                    raise

                size = _encoded_size(value)
                if size > effective_limits.max_output_bytes:
                    for future in futures.values():
                        future.cancel()
                    raise ResourceLimitError(
                        f"節點 {node.node_id} 輸出 {size} bytes，"
                        f"超過單節點上限 {effective_limits.max_output_bytes}。"
                    )
                if total_output_bytes + size > effective_limits.max_total_output_bytes:
                    for future in futures.values():
                        future.cancel()
                    raise ResourceLimitError(
                        f"累積輸出將達 {total_output_bytes + size} bytes，"
                        f"超過總上限 {effective_limits.max_total_output_bytes}。"
                    )

                inherited = {
                    label
                    for dependency in node.dependencies
                    for label in taints.get(dependency, ())
                }
                node_taints = tuple(sorted(inherited | set(node.taint_sources)))
                outputs[node.node_id] = value
                taints[node.node_id] = node_taints
                output_bytes[node.node_id] = size
                total_output_bytes += size

                if on_complete is not None:
                    on_complete(
                        ExecutionEvent(
                            node_id=node.node_id,
                            value=value,
                            taints=node_taints,
                            output_bytes=size,
                            layer=layer_number,
                        )
                    )

    return ExecutionTrace(
        outputs=outputs,
        taints=taints,
        output_bytes=output_bytes,
        total_output_bytes=total_output_bytes,
        execution_layers=tuple(
            tuple(node.node_id for node in layer) for layer in layers
        ),
    )


def final_result(program: Program, outputs: dict[str, Any], output_node: str | None = None) -> Any:
    if output_node is not None:
        if output_node not in outputs:
            raise KeyError(f"指定的輸出節點不存在：{output_node}")
        return outputs[output_node]

    sinks = program.sink_nodes()
    if len(sinks) == 1:
        return outputs[sinks[0].node_id]
    return {node.node_id: outputs[node.node_id] for node in sinks}


def _encoded_size(value: Any) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def _requires_serial_effects(node: Node) -> bool:
    return bool(
        {
            "database.write",
            "filesystem.write",
            "filesystem.delete",
        }
        & set(node.effects)
    )
