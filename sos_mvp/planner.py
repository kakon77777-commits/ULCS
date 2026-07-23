from __future__ import annotations

from .executors import ExecutionError, get_adapter
from .extensions import ensure_runtime_extensions
from .model import GraphError, Program
from .provenance import digest_value
from .resource_analysis import analyze_claims, infer_taint_sources
from .sqlite_runtime import install_closing_sqlite_adapter


_EXTERNAL_EFFECT_PREFIXES = ("filesystem.", "network.", "database.")
_NONDETERMINISTIC_EFFECTS = {"process.spawn.possible"}


def _input_type(node, by_id) -> str:
    if not node.inputs:
        return "None"
    if len(node.inputs) > 1:
        return "InputMap"
    return by_id[node.inputs[0].node_id].output_type


def _deterministic(adapter) -> bool:
    declared = getattr(adapter, "deterministic", None)
    if declared is not None:
        return bool(declared)
    return adapter.language in {"regex", "jq"}


def _cacheable(deterministic: bool, effects: list[str]) -> bool:
    if not deterministic:
        return False
    return not any(
        effect.startswith(_EXTERNAL_EFFECT_PREFIXES)
        or effect in _NONDETERMINISTIC_EFFECTS
        for effect in effects
    )


def enrich_and_validate(program: Program) -> Program:
    ensure_runtime_extensions()
    install_closing_sqlite_adapter()
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
        node.claims = analyze_claims(node.language, node.code, node.effects)
        node.taint_sources = infer_taint_sources(node.claims)
        node.deterministic = _deterministic(adapter)
        node.cacheable = _cacheable(node.deterministic, node.effects)
    return program


def _schema_label(schema) -> str:
    if schema is None:
        return "none"
    return digest_value(schema)[:12]


def plan_lines(program: Program) -> list[str]:
    lines: list[str] = []
    layer_index = {
        node.node_id: index
        for index, layer in enumerate(program.execution_layers(), start=1)
        for node in layer
    }
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
        claims = ", ".join(claim.token for claim in node.claims) or "none"
        taints = ", ".join(node.taint_sources) or "none"
        repeatability = (
            "deterministic/cacheable"
            if node.cacheable
            else "deterministic/no-cache"
            if node.deterministic
            else "runtime-dependent"
        )
        artifact = (
            f"input-schema={_schema_label(node.input_schema)}, "
            f"output-schema={_schema_label(node.output_schema)}, "
            f"persist={node.persist_output}"
        )
        lines.append(
            f"{index}. L{layer_index[node.node_id]} {node.node_id} [{node.language}] "
            f"{node.input_type} → {node.output_type}; 來源={source}; "
            f"Runtime={node.runtime}; 能力={effects}; 範圍={claims}; "
            f"污染源={taints}; 重現性={repeatability}; Artifact={artifact}"
        )
    return lines
