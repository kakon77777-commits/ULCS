from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


class ResourceLimitError(RuntimeError):
    """Raised when an execution resource quota is exceeded."""


@dataclass(frozen=True, slots=True, order=True)
class CapabilityClaim:
    capability: str
    resource: str = "*"

    @property
    def token(self) -> str:
        return f"{self.capability}@{self.resource}"

    def to_dict(self) -> dict[str, str]:
        return {"capability": self.capability, "resource": self.resource}


@dataclass(frozen=True, slots=True)
class ExecutionLimits:
    max_nodes: int = 256
    max_workers: int = 1
    max_output_bytes: int = 8 * 1024 * 1024
    max_total_output_bytes: int = 32 * 1024 * 1024

    def __post_init__(self) -> None:
        for name, value in (
            ("max_nodes", self.max_nodes),
            ("max_workers", self.max_workers),
            ("max_output_bytes", self.max_output_bytes),
            ("max_total_output_bytes", self.max_total_output_bytes),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} 必須是大於 0 的整數。")
        if self.max_output_bytes > self.max_total_output_bytes:
            raise ValueError("max_output_bytes 不可大於 max_total_output_bytes。")

    @classmethod
    def from_mapping(cls, value: object | None) -> "ExecutionLimits":
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise ValueError("limits 必須是 JSON object。")
        allowed = {
            "max_nodes",
            "max_workers",
            "max_output_bytes",
            "max_total_output_bytes",
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(f"未知 limits 欄位：{', '.join(unknown)}")
        return cls(**{name: int(raw) for name, raw in value.items()})

    def with_overrides(
        self,
        *,
        max_nodes: int | None = None,
        max_workers: int | None = None,
        max_output_bytes: int | None = None,
        max_total_output_bytes: int | None = None,
    ) -> "ExecutionLimits":
        return ExecutionLimits(
            max_nodes=self.max_nodes if max_nodes is None else max_nodes,
            max_workers=self.max_workers if max_workers is None else max_workers,
            max_output_bytes=(
                self.max_output_bytes if max_output_bytes is None else max_output_bytes
            ),
            max_total_output_bytes=(
                self.max_total_output_bytes
                if max_total_output_bytes is None
                else max_total_output_bytes
            ),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "max_nodes": self.max_nodes,
            "max_workers": self.max_workers,
            "max_output_bytes": self.max_output_bytes,
            "max_total_output_bytes": self.max_total_output_bytes,
        }

    def summary(self) -> str:
        return (
            f"nodes≤{self.max_nodes}; workers≤{self.max_workers}; "
            f"output≤{self.max_output_bytes}B/node; "
            f"total≤{self.max_total_output_bytes}B"
        )
