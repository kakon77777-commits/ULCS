from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .model import Node, Program


KNOWN_CAPABILITIES = (
    "database.read",
    "database.write",
    "filesystem.read",
    "filesystem.write",
    "filesystem.delete",
    "filesystem.possible",
    "javascript.execute",
    "network.access",
    "network.possible",
    "process.execute",
    "process.spawn.possible",
    "python.execute",
)


class CapabilityError(ValueError):
    """Raised when a capability policy is malformed."""


class CapabilityDeniedError(PermissionError):
    """Raised before execution when a node requests forbidden capabilities."""

    def __init__(self, decision: "CapabilityDecision") -> None:
        self.decision = decision
        denied = ", ".join(decision.denied) or "unknown"
        super().__init__(f"節點 {decision.node_id} 的能力未獲授權：{denied}")


@dataclass(frozen=True, slots=True)
class CapabilityDecision:
    node_id: str
    required: tuple[str, ...]
    allowed: tuple[str, ...]
    denied: tuple[str, ...]
    audited: tuple[str, ...]

    @property
    def permitted(self) -> bool:
        return not self.denied


@dataclass(frozen=True, slots=True)
class CapabilityPolicy:
    """Execution policy for ULCS effects.

    In ``audit`` mode, capabilities missing from the allow-list are reported but
    do not block execution. Explicit deny patterns always block. In ``enforce``
    mode, every required capability must match an allow pattern and no deny
    pattern.
    """

    mode: str = "audit"
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    source: str = "default"

    def __post_init__(self) -> None:
        if self.mode not in {"audit", "enforce"}:
            raise CapabilityError("能力政策 mode 必須是 audit 或 enforce。")
        for label, patterns in (("allow", self.allow), ("deny", self.deny)):
            for pattern in patterns:
                if not pattern or any(ch.isspace() for ch in pattern):
                    raise CapabilityError(f"{label} 能力模式不可為空或包含空白：{pattern!r}")

    @classmethod
    def from_file(cls, path: str | Path) -> "CapabilityPolicy":
        policy_path = Path(path)
        try:
            payload = json.loads(policy_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CapabilityError(f"能力政策不是合法 JSON：{exc}") from exc
        if not isinstance(payload, dict):
            raise CapabilityError("能力政策根節點必須是 JSON object。")
        return cls(
            mode=str(payload.get("mode", "enforce")).lower(),
            allow=_as_patterns(payload.get("allow", []), "allow"),
            deny=_as_patterns(payload.get("deny", []), "deny"),
            source=str(policy_path.resolve()),
        )

    @classmethod
    def compose(
        cls,
        *,
        policy_path: str | Path | None = None,
        allow: Iterable[str] = (),
        deny: Iterable[str] = (),
        enforce: bool = False,
    ) -> "CapabilityPolicy":
        base = cls.from_file(policy_path) if policy_path else cls()
        mode = "enforce" if enforce or policy_path else base.mode
        return cls(
            mode=mode,
            allow=tuple(dict.fromkeys((*base.allow, *allow))),
            deny=tuple(dict.fromkeys((*base.deny, *deny))),
            source=base.source if policy_path else "command-line/default",
        )

    def decide(self, node: Node) -> CapabilityDecision:
        required = tuple(sorted(set(node.effects)))
        allowed: list[str] = []
        denied: list[str] = []
        audited: list[str] = []

        for capability in required:
            explicitly_denied = _matches(capability, self.deny)
            explicitly_allowed = _matches(capability, self.allow)
            if explicitly_denied:
                denied.append(capability)
            elif explicitly_allowed:
                allowed.append(capability)
            elif self.mode == "enforce":
                denied.append(capability)
            else:
                audited.append(capability)

        return CapabilityDecision(
            node_id=node.node_id,
            required=required,
            allowed=tuple(allowed),
            denied=tuple(denied),
            audited=tuple(audited),
        )

    def check_node(self, node: Node) -> CapabilityDecision:
        decision = self.decide(node)
        if not decision.permitted:
            raise CapabilityDeniedError(decision)
        return decision

    def evaluate_program(self, program: Program) -> list[CapabilityDecision]:
        return [self.decide(node) for node in program.topological_nodes()]

    def check_program(self, program: Program) -> list[CapabilityDecision]:
        decisions = self.evaluate_program(program)
        for decision in decisions:
            if not decision.permitted:
                raise CapabilityDeniedError(decision)
        return decisions

    def summary(self) -> str:
        allow = ", ".join(self.allow) or "(none)"
        deny = ", ".join(self.deny) or "(none)"
        return f"mode={self.mode}; allow={allow}; deny={deny}; source={self.source}"


def _matches(capability: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(capability, pattern) for pattern in patterns)


def _as_patterns(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CapabilityError(f"能力政策 {field_name} 必須是字串陣列。")
    return tuple(dict.fromkeys(value))


def decision_line(decision: CapabilityDecision) -> str:
    if not decision.required:
        return f"{decision.node_id}: pure"
    if decision.denied:
        return f"{decision.node_id}: DENY {', '.join(decision.denied)}"
    if decision.audited:
        return f"{decision.node_id}: AUDIT {', '.join(decision.audited)}"
    return f"{decision.node_id}: ALLOW {', '.join(decision.allowed)}"
