from __future__ import annotations

import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .capabilities import CapabilityPolicy
from .executors import execute_node, resolve_input
from .model import Node, Program
from .provenance import (
    CacheConfig,
    ContentAddressedCache,
    ExecutionManifest,
    digest_value,
    node_fingerprint,
    plan_digest,
    policy_digest,
    program_digest,
)
from .resources import ExecutionLimits, ResourceLimitError


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    node_id: str
    value: Any
    taints: tuple[str, ...] = ()
    output_bytes: int = 0
    layer: int = 0
    cache_hit: bool = False
    fingerprint: str = ""
    output_digest: str = ""


@dataclass(frozen=True, slots=True)
class ExecutionTrace:
    outputs: dict[str, Any]
    taints: dict[str, tuple[str, ...]]
    output_bytes: dict[str, int]
    total_output_bytes: int
    execution_layers: tuple[tuple[str, ...], ...]
    node_fingerprints: dict[str, str]
    input_digests: dict[str, str]
    output_digests: dict[str, str]
    cache_hits: dict[str, bool]
    cache_mode: str
    manifest: ExecutionManifest

    def to_dict(self) -> dict[str, Any]:
        return {
            "outputs": self.outputs,
            "taints": {key: list(value) for key, value in self.taints.items()},
            "output_bytes": dict(self.output_bytes),
            "total_output_bytes": self.total_output_bytes,
            "execution_layers": [list(layer) for layer in self.execution_layers],
            "node_fingerprints": dict(self.node_fingerprints),
            "input_digests": dict(self.input_digests),
            "output_digests": dict(self.output_digests),
            "cache_hits": dict(self.cache_hits),
            "cache_mode": self.cache_mode,
            "manifest": self.manifest.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class _NodeRunResult:
    value: Any
    fingerprint: str
    input_digest: str
    output_digest: str
    cache_hit: bool


def execute_program(
    program: Program,
    *,
    cwd: Path,
    db_path: Path,
    timeout: int = 60,
    policy: CapabilityPolicy | None = None,
    limits: ExecutionLimits | None = None,
    cache_config: CacheConfig | None = None,
    on_complete: Callable[[ExecutionEvent], None] | None = None,
) -> dict[str, Any]:
    return execute_program_with_trace(
        program,
        cwd=cwd,
        db_path=db_path,
        timeout=timeout,
        policy=policy,
        limits=limits,
        cache_config=cache_config,
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
    cache_config: CacheConfig | None = None,
    on_complete: Callable[[ExecutionEvent], None] | None = None,
) -> ExecutionTrace:
    """Execute a validated DAG with bounded parallelism and provenance.

    Authorization and node-count quotas are checked before any Runtime starts.
    Only nodes marked deterministic and cacheable by the validated plan may use
    the content-addressed cache. Mutating filesystem/database nodes remain
    conservatively serialized.
    """
    if policy is not None:
        policy.check_program(program)
    effective_limits = limits or (policy.limits if policy is not None else ExecutionLimits())
    effective_cache = cache_config or CacheConfig()
    cache = ContentAddressedCache(effective_cache)

    if len(program.nodes) > effective_limits.max_nodes:
        raise ResourceLimitError(
            f"節點數 {len(program.nodes)} 超過上限 {effective_limits.max_nodes}。"
        )

    outputs: dict[str, Any] = {}
    taints: dict[str, tuple[str, ...]] = {}
    output_bytes: dict[str, int] = {}
    node_fingerprints: dict[str, str] = {}
    input_digests: dict[str, str] = {}
    output_digests: dict[str, str] = {}
    cache_hits: dict[str, bool] = {}
    total_output_bytes = 0
    serial_effect_lock = threading.Lock()
    layers = program.execution_layers()
    current_program_digest = program_digest(program)
    current_plan_digest = plan_digest(program)
    current_policy_digest = policy_digest(policy)

    for layer_number, layer in enumerate(layers, start=1):
        worker_count = min(effective_limits.max_workers, len(layer))
        futures: dict[str, Future[_NodeRunResult]] = {}

        def run(node: Node) -> _NodeRunResult:
            input_value = resolve_input(node, outputs)
            fingerprint, input_digest = node_fingerprint(node, input_value)
            if node.cacheable:
                entry = cache.load(fingerprint)
                if entry is not None:
                    return _NodeRunResult(
                        value=entry.value,
                        fingerprint=fingerprint,
                        input_digest=input_digest,
                        output_digest=entry.output_digest,
                        cache_hit=True,
                    )

            if _requires_serial_effects(node):
                with serial_effect_lock:
                    value = execute_node(node, outputs, cwd, db_path, timeout=timeout)
            else:
                value = execute_node(node, outputs, cwd, db_path, timeout=timeout)

            output_digest = digest_value(value)
            if node.cacheable:
                stored = cache.store(fingerprint, value)
                if stored is not None:
                    value = stored.value
                    output_digest = stored.output_digest
            return _NodeRunResult(
                value=value,
                fingerprint=fingerprint,
                input_digest=input_digest,
                output_digest=output_digest,
                cache_hit=False,
            )

        with ThreadPoolExecutor(
            max_workers=max(1, worker_count),
            thread_name_prefix=f"ulcs-layer-{layer_number}",
        ) as executor:
            for node in layer:
                futures[node.node_id] = executor.submit(run, node)

            for node in layer:
                try:
                    result = futures[node.node_id].result()
                except Exception:
                    for future in futures.values():
                        future.cancel()
                    raise

                size = _encoded_size(result.value)
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
                outputs[node.node_id] = result.value
                taints[node.node_id] = node_taints
                output_bytes[node.node_id] = size
                node_fingerprints[node.node_id] = result.fingerprint
                input_digests[node.node_id] = result.input_digest
                output_digests[node.node_id] = result.output_digest
                cache_hits[node.node_id] = result.cache_hit
                total_output_bytes += size

                if on_complete is not None:
                    on_complete(
                        ExecutionEvent(
                            node_id=node.node_id,
                            value=result.value,
                            taints=node_taints,
                            output_bytes=size,
                            layer=layer_number,
                            cache_hit=result.cache_hit,
                            fingerprint=result.fingerprint,
                            output_digest=result.output_digest,
                        )
                    )

    execution_layers = tuple(tuple(node.node_id for node in layer) for layer in layers)
    manifest_nodes = {
        node.node_id: {
            "fingerprint": node_fingerprints[node.node_id],
            "input_digest": input_digests[node.node_id],
            "output_digest": output_digests[node.node_id],
            "runtime": node.runtime,
            "claims": [claim.token for claim in node.claims],
            "taints": list(taints[node.node_id]),
            "deterministic": node.deterministic,
            "cacheable": node.cacheable,
            "cache_hit": cache_hits[node.node_id],
            "output_bytes": output_bytes[node.node_id],
        }
        for node in program.topological_nodes()
    }
    manifest = ExecutionManifest(
        program_digest=current_program_digest,
        plan_digest=current_plan_digest,
        policy_digest=current_policy_digest,
        execution_layers=execution_layers,
        nodes=manifest_nodes,
    )
    return ExecutionTrace(
        outputs=outputs,
        taints=taints,
        output_bytes=output_bytes,
        total_output_bytes=total_output_bytes,
        execution_layers=execution_layers,
        node_fingerprints=node_fingerprints,
        input_digests=input_digests,
        output_digests=output_digests,
        cache_hits=cache_hits,
        cache_mode=effective_cache.mode,
        manifest=manifest,
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
