from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .resources import CapabilityClaim


class GraphError(ValueError):
    """Raised when a Language Operator Graph is structurally invalid."""


@dataclass(frozen=True, slots=True)
class InputRef:
    node_id: str
    field: str | None = None
    alias: str | None = None

    @property
    def key(self) -> str:
        return self.alias or self.node_id


@dataclass(slots=True)
class Node:
    node_id: str
    role: str
    language: str
    code: str
    inputs: list[InputRef] = field(default_factory=list)
    input_type: str = "None"
    output_type: str = "Any"
    effects: list[str] = field(default_factory=list)
    runtime: str = ""
    claims: list[CapabilityClaim] = field(default_factory=list)
    taint_sources: list[str] = field(default_factory=list)
    deterministic: bool = False
    cacheable: bool = False

    @property
    def input_ref(self) -> InputRef | None:
        """v0.1 compatibility: return the first input reference."""
        return self.inputs[0] if self.inputs else None

    @property
    def dependencies(self) -> tuple[str, ...]:
        return tuple(ref.node_id for ref in self.inputs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "role": self.role,
            "language": self.language,
            "code": self.code,
            "inputs": [
                {
                    "node_id": ref.node_id,
                    "field": ref.field,
                    "alias": ref.alias,
                }
                for ref in self.inputs
            ],
            "input_type": self.input_type,
            "output_type": self.output_type,
            "effects": list(self.effects),
            "capabilities": list(self.effects),
            "runtime": self.runtime,
            "claims": [claim.to_dict() for claim in self.claims],
            "taint_sources": list(self.taint_sources),
            "deterministic": self.deterministic,
            "cacheable": self.cacheable,
        }


@dataclass(slots=True)
class Program:
    nodes: list[Node]

    def node_map(self) -> dict[str, Node]:
        result: dict[str, Node] = {}
        for node in self.nodes:
            if node.node_id in result:
                raise GraphError(f"節點名稱重複：{node.node_id}")
            result[node.node_id] = node
        return result

    def _graph_state(
        self,
    ) -> tuple[dict[str, Node], dict[str, int], dict[str, list[str]], dict[str, int]]:
        by_id = self.node_map()
        order_index = {node.node_id: index for index, node in enumerate(self.nodes)}
        indegree = {node_id: 0 for node_id in by_id}
        children: dict[str, list[str]] = {node_id: [] for node_id in by_id}

        for node in self.nodes:
            seen: set[str] = set()
            for ref in node.inputs:
                if ref.node_id not in by_id:
                    raise GraphError(f"節點 {node.node_id} 引用了不存在的節點 {ref.node_id}")
                if ref.node_id == node.node_id:
                    raise GraphError(f"節點 {node.node_id} 不可引用自身")
                if ref.node_id in seen:
                    raise GraphError(f"節點 {node.node_id} 重複引用 {ref.node_id}")
                seen.add(ref.node_id)
                indegree[node.node_id] += 1
                children[ref.node_id].append(node.node_id)
        return by_id, indegree, children, order_index

    def execution_layers(self) -> list[list[Node]]:
        by_id, indegree, children, order_index = self._graph_state()
        ready = sorted(
            (node_id for node_id, degree in indegree.items() if degree == 0),
            key=order_index.__getitem__,
        )
        layers: list[list[Node]] = []
        visited = 0

        while ready:
            current_layer = list(ready)
            layers.append([by_id[node_id] for node_id in current_layer])
            visited += len(current_layer)
            next_ready: list[str] = []
            for current in current_layer:
                for child in sorted(children[current], key=order_index.__getitem__):
                    indegree[child] -= 1
                    if indegree[child] == 0:
                        next_ready.append(child)
            ready = sorted(next_ready, key=order_index.__getitem__)

        if visited != len(self.nodes):
            cycle_nodes = [node_id for node_id, degree in indegree.items() if degree > 0]
            raise GraphError(f"語言算子圖存在循環：{', '.join(cycle_nodes)}")
        return layers

    def topological_nodes(self) -> list[Node]:
        return [node for layer in self.execution_layers() for node in layer]

    def sink_nodes(self) -> list[Node]:
        referenced = {ref.node_id for node in self.nodes for ref in node.inputs}
        return [node for node in self.nodes if node.node_id not in referenced]

    def to_dict(self) -> dict[str, Any]:
        edges = [
            {
                "from": ref.node_id,
                "from_field": ref.field,
                "to": node.node_id,
                "input_key": ref.key,
            }
            for node in self.nodes
            for ref in node.inputs
        ]
        layers = self.execution_layers()
        return {
            "format": "ULCS-Language-Operator-Graph",
            "version": "0.5",
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": edges,
            "execution_order": [node.node_id for layer in layers for node in layer],
            "execution_layers": [[node.node_id for node in layer] for layer in layers],
            "sinks": [node.node_id for node in self.sink_nodes()],
        }
