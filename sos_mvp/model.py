from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class InputRef:
    node_id: str
    field: str | None = None


@dataclass(slots=True)
class Node:
    node_id: str
    role: str
    language: str
    code: str
    input_ref: InputRef | None = None
    input_type: str = "None"
    output_type: str = "Any"
    effects: list[str] = field(default_factory=list)
    runtime: str = ""

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        return value


@dataclass(slots=True)
class Program:
    nodes: list[Node]

    def to_dict(self) -> dict[str, Any]:
        return {"format": "SOS-Language-Operator-Graph", "version": "0.1", "nodes": [n.to_dict() for n in self.nodes]}
