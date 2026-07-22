from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .model import Node, Program
from .resources import CapabilityClaim, ExecutionLimits


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
        denied = ", ".join(decision.denied_claims or decision.denied) or "unknown"
        super().__init__(f"節點 {decision.node_id} 的能力或資源範圍未獲授權：{denied}")


@dataclass(frozen=True, slots=True)
class CapabilityDecision:
    node_id: str
    required: tuple[str, ...]
    allowed: tuple[str, ...]
    denied: tuple[str, ...]
    audited: tuple[str, ...]
    required_claims: tuple[str, ...] = ()
    allowed_claims: tuple[str, ...] = ()
    denied_claims: tuple[str, ...] = ()
    audited_claims: tuple[str, ...] = ()

    @property
    def permitted(self) -> bool:
        return not self.denied_claims and not self.denied


@dataclass(frozen=True, slots=True)
class CapabilityPolicy:
    """Execution policy for capabilities, resource scopes, and quotas.

    Patterns without ``@`` match a capability across every resource, preserving
    v0.3 behavior. Scoped patterns use ``capability@resource``.
    """

    mode: str = "audit"
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    source: str = "default"
    limits: ExecutionLimits = field(default_factory=ExecutionLimits)

    def __post_init__(self) -> None:
        if self.mode not in {"audit", "enforce"}:
            raise CapabilityError("能力政策 mode 必須是 audit 或 enforce。")
        for label, patterns in (("allow", self.allow), ("deny", self.deny)):
            for pattern in patterns:
                if not pattern or any(ch.isspace() for ch in pattern):
                    raise CapabilityError(f"{label} 能力模式不可為空或包含空白：{pattern!r}")
                capability_pattern, resource_pattern = _split_rule(pattern)
                if not capability_pattern or not resource_pattern:
                    raise CapabilityError(f"{label} 能力範圍格式錯誤：{pattern!r}")

    @classmethod
    def from_file(cls, path: str | Path) -> "CapabilityPolicy":
        policy_path = Path(path)
        try:
            payload = json.loads(policy_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CapabilityError(f"能力政策不是合法 JSON：{exc}") from exc
        if not isinstance(payload, dict):
            raise CapabilityError("能力政策根節點必須是 JSON object。")
        try:
            limits = ExecutionLimits.from_mapping(payload.get("limits"))
        except (TypeError, ValueError) as exc:
            raise CapabilityError(f"能力政策 limits 無效：{exc}") from exc
        return cls(
            mode=str(payload.get("mode", "enforce")).lower(),
            allow=_as_patterns(payload.get("allow", []), "allow"),
            deny=_as_patterns(payload.get("deny", []), "deny"),
            source=str(policy_path.resolve()),
            limits=limits,
        )

    @classmethod
    def compose(
        cls,
        *,
        policy_path: str | Path | None = None,
        allow: Iterable[str] = (),
        deny: Iterable[str] = (),
        enforce: bool = False,
        max_nodes: int | None = None,
        max_workers: int | None = None,
        max_output_bytes: int | None = None,
        max_total_output_bytes: int | None = None,
    ) -> "CapabilityPolicy":
        base = cls.from_file(policy_path) if policy_path else cls()
        mode = "enforce" if enforce else base.mode
        try:
            limits = base.limits.with_overrides(
                max_nodes=max_nodes,
                max_workers=max_workers,
                max_output_bytes=max_output_bytes,
                max_total_output_bytes=max_total_output_bytes,
            )
        except ValueError as exc:
            raise CapabilityError(str(exc)) from exc
        return cls(
            mode=mode,
            allow=tuple(dict.fromkeys((*base.allow, *allow))),
            deny=tuple(dict.fromkeys((*base.deny, *deny))),
            source=base.source if policy_path else "command-line/default",
            limits=limits,
        )

    def decide(self, node: Node) -> CapabilityDecision:
        claims = tuple(
            sorted(
                set(node.claims)
                or {CapabilityClaim(capability, "*") for capability in node.effects}
            )
        )
        allowed_claims: list[str] = []
        denied_claims: list[str] = []
        audited_claims: list[str] = []

        capability_status: dict[str, set[str]] = {}
        for claim in claims:
            explicitly_denied = _matches_claim(claim, self.deny)
            explicitly_allowed = _matches_claim(claim, self.allow)
            if explicitly_denied:
                status = "denied"
                denied_claims.append(claim.token)
            elif explicitly_allowed:
                status = "allowed"
                allowed_claims.append(claim.token)
            elif self.mode == "enforce":
                status = "denied"
                denied_claims.append(claim.token)
            else:
                status = "audited"
                audited_claims.append(claim.token)
            capability_status.setdefault(claim.capability, set()).add(status)

        required = tuple(sorted(capability_status))
        denied = tuple(
            capability
            for capability in required
            if "denied" in capability_status[capability]
        )
        audited = tuple(
            capability
            for capability in required
            if capability not in denied and "audited" in capability_status[capability]
        )
        allowed = tuple(
            capability
            for capability in required
            if capability not in denied and capability not in audited
        )

        return CapabilityDecision(
            node_id=node.node_id,
            required=required,
            allowed=allowed,
            denied=denied,
            audited=audited,
            required_claims=tuple(claim.token for claim in claims),
            allowed_claims=tuple(allowed_claims),
            denied_claims=tuple(denied_claims),
            audited_claims=tuple(audited_claims),
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
        return (
            f"mode={self.mode}; allow={allow}; deny={deny}; "
            f"limits={self.limits.summary()}; source={self.source}"
        )


def _split_rule(pattern: str) -> tuple[str, str]:
    if "@" not in pattern:
        return pattern, "*"
    capability, resource = pattern.split("@", 1)
    return capability, resource


def _matches_claim(claim: CapabilityClaim, patterns: tuple[str, ...]) -> bool:
    for pattern in patterns:
        capability_pattern, resource_pattern = _split_rule(pattern)
        if fnmatch.fnmatchcase(claim.capability, capability_pattern) and fnmatch.fnmatchcase(
            claim.resource, resource_pattern
        ):
            return True
    return False


def _as_patterns(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CapabilityError(f"能力政策 {field_name} 必須是字串陣列。")
    return tuple(dict.fromkeys(value))


def decision_line(decision: CapabilityDecision) -> str:
    if not decision.required_claims:
        return f"{decision.node_id}: pure"
    if decision.denied_claims:
        return f"{decision.node_id}: DENY {', '.join(decision.denied_claims)}"
    if decision.audited_claims:
        return f"{decision.node_id}: AUDIT {', '.join(decision.audited_claims)}"
    return f"{decision.node_id}: ALLOW {', '.join(decision.allowed_claims)}"
