from __future__ import annotations

import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from .artifacts import (
    ArtifactConfig,
    ArtifactRef,
    ArtifactStore,
    CheckpointError,
    ExecutionCheckpoint,
    SchemaValidationError,
    validate_schema,
)
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
    resumed: bool = False
    fingerprint: str = ""
    output_digest: str = ""
    artifact: ArtifactRef | None = None


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
    resumed: dict[str, bool]
    artifacts: dict[str, ArtifactRef | None]
    cache_mode: str
    checkpoint_path: str | None
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
            "resumed": dict(self.resumed),
            "artifacts": {
                key: (ref.to_dict() if ref is not None else None)
                for key, ref in self.artifacts.items()
            },
            "cache_mode": self.cache_mode,
            "checkpoint_path": self.checkpoint_path,
            "manifest": self.manifest.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class _NodeRunResult:
    value: Any
    fingerprint: str
    input_digest: str
    output_digest: str
    cache_hit: bool
    resumed: bool = False
    artifact: ArtifactRef | None = None


def execute_program(
    program: Program,
    *,
    cwd: Path,
    db_path: Path,
    timeout: int = 60,
    policy: CapabilityPolicy | None = None,
    limits: ExecutionLimits | None = None,
    cache_config: CacheConfig | None = None,
    artifact_config: ArtifactConfig | None = None,
    checkpoint_path: Path | None = None,
    resume_checkpoint: ExecutionCheckpoint | None = None,
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
        artifact_config=artifact_config,
        checkpoint_path=checkpoint_path,
        resume_checkpoint=resume_checkpoint,
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
    artifact_config: ArtifactConfig | None = None,
    checkpoint_path: Path | None = None,
    resume_checkpoint: ExecutionCheckpoint | None = None,
    on_complete: Callable[[ExecutionEvent], None] | None = None,
) -> ExecutionTrace:
    """Execute a validated DAG with bounded parallelism and durable artifacts."""
    if policy is not None:
        policy.check_program(program)
    effective_limits = limits or (policy.limits if policy is not None else ExecutionLimits())
    effective_cache = cache_config or CacheConfig()
    cache = ContentAddressedCache(effective_cache)

    if len(program.nodes) > effective_limits.max_nodes:
        raise ResourceLimitError(
            f"節點數 {len(program.nodes)} 超過上限 {effective_limits.max_nodes}。"
        )

    if checkpoint_path is not None or resume_checkpoint is not None:
        if artifact_config is None:
            artifact_config = ArtifactConfig(directory=cwd / ".ulcs-artifacts", persist_all=True)
        elif not artifact_config.persist_all:
            artifact_config = replace(artifact_config, persist_all=True)
    artifact_store = ArtifactStore(artifact_config) if artifact_config is not None else None

    outputs: dict[str, Any] = {}
    taints: dict[str, tuple[str, ...]] = {}
    output_bytes: dict[str, int] = {}
    node_fingerprints: dict[str, str] = {}
    input_digests: dict[str, str] = {}
    output_digests: dict[str, str] = {}
    cache_hits: dict[str, bool] = {}
    resumed: dict[str, bool] = {}
    artifacts: dict[str, ArtifactRef | None] = {}
    total_output_bytes = 0
    serial_effect_lock = threading.Lock()
    layers = program.execution_layers()
    current_program_digest = program_digest(program)
    current_plan_digest = plan_digest(program)
    current_policy_digest = policy_digest(policy)

    if resume_checkpoint is not None:
        if artifact_store is None:
            raise CheckpointError("resume 需要 Artifact Store。")
        resume_checkpoint.verify_plan(
            current_program_digest,
            current_plan_digest,
            current_policy_digest,
        )

    for layer_number, layer in enumerate(layers, start=1):
        worker_count = min(effective_limits.max_workers, len(layer))
        futures: dict[str, Future[_NodeRunResult]] = {}

        def run(node: Node) -> _NodeRunResult:
            input_value = resolve_input(node, outputs)
            try:
                validate_schema(input_value, node.input_schema)
            except SchemaValidationError as exc:
                raise SchemaValidationError(f"節點 {node.node_id} input schema 失敗：{exc}") from exc
            fingerprint, input_digest = node_fingerprint(node, input_value)

            if resume_checkpoint is not None and node.node_id in resume_checkpoint.nodes:
                saved = resume_checkpoint.nodes[node.node_id]
                if str(saved.get("fingerprint", "")) != fingerprint:
                    raise CheckpointError(f"節點 {node.node_id} 的 checkpoint fingerprint 不一致。")
                raw_ref = saved.get("artifact")
                if not isinstance(raw_ref, dict):
                    raise CheckpointError(f"節點 {node.node_id} 的 checkpoint 缺少 artifact。")
                ref = ArtifactRef.from_mapping(raw_ref)
                assert artifact_store is not None
                value = artifact_store.load(ref, node.output_schema)
                output_digest = digest_value(value)
                if output_digest != str(saved.get("output_digest", "")):
                    raise CheckpointError(f"節點 {node.node_id} 的 checkpoint output digest 不一致。")
                return _NodeRunResult(
                    value=value,
                    fingerprint=fingerprint,
                    input_digest=input_digest,
                    output_digest=output_digest,
                    cache_hit=False,
                    resumed=True,
                    artifact=ref,
                )

            if node.cacheable:
                entry = cache.load(fingerprint)
                if entry is not None:
                    try:
                        validate_schema(entry.value, node.output_schema)
                    except SchemaValidationError:
                        entry = None
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

            try:
                validate_schema(value, node.output_schema)
            except SchemaValidationError as exc:
                raise SchemaValidationError(f"節點 {node.node_id} output schema 失敗：{exc}") from exc

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

                artifact = result.artifact
                if (
                    artifact is None
                    and artifact_store is not None
                    and artifact_store.should_persist(size=size, explicit=node.persist_output)
                ):
                    artifact = artifact_store.store(result.value, node.output_schema)

                inherited = {
                    label
                    for dependency in node.dependencies
                    for label in taints.get(dependency, ())
                }
                if result.resumed and resume_checkpoint is not None:
                    saved_taints = resume_checkpoint.nodes[node.node_id].get("taints", [])
                    node_taints = tuple(sorted(str(item) for item in saved_taints))
                else:
                    node_taints = tuple(sorted(inherited | set(node.taint_sources)))
                outputs[node.node_id] = result.value
                taints[node.node_id] = node_taints
                output_bytes[node.node_id] = size
                node_fingerprints[node.node_id] = result.fingerprint
                input_digests[node.node_id] = result.input_digest
                output_digests[node.node_id] = result.output_digest
                cache_hits[node.node_id] = result.cache_hit
                resumed[node.node_id] = result.resumed
                artifacts[node.node_id] = artifact
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
                            resumed=result.resumed,
                            fingerprint=result.fingerprint,
                            output_digest=result.output_digest,
                            artifact=artifact,
                        )
                    )

        if checkpoint_path is not None:
            if artifact_store is None:
                raise CheckpointError("checkpoint 需要 Artifact Store。")
            checkpoint_nodes: dict[str, dict[str, Any]] = {}
            for completed in program.topological_nodes():
                node_id = completed.node_id
                if node_id not in outputs:
                    continue
                ref = artifacts.get(node_id)
                if ref is None:
                    ref = artifact_store.store(outputs[node_id], completed.output_schema)
                    artifacts[node_id] = ref
                checkpoint_nodes[node_id] = {
                    "fingerprint": node_fingerprints[node_id],
                    "input_digest": input_digests[node_id],
                    "output_digest": output_digests[node_id],
                    "taints": list(taints[node_id]),
                    "artifact": ref.to_dict(),
                }
            ExecutionCheckpoint(
                program_digest=current_program_digest,
                plan_digest=current_plan_digest,
                policy_digest=current_policy_digest,
                nodes=checkpoint_nodes,
            ).write(checkpoint_path)

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
            "resumed": resumed[node.node_id],
            "output_bytes": output_bytes[node.node_id],
            "artifact_digest": (
                artifacts[node.node_id].digest if artifacts[node.node_id] is not None else None
            ),
            "schema_digest": (
                artifacts[node.node_id].schema_digest if artifacts[node.node_id] is not None else None
            ),
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
        resumed=resumed,
        artifacts=artifacts,
        cache_mode=effective_cache.mode,
        checkpoint_path=str(checkpoint_path) if checkpoint_path is not None else None,
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
