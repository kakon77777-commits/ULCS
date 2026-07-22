from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

CACHE_FORMAT = "ULCS-Content-Addressed-Cache"
CACHE_VERSION = "0.5"
MANIFEST_FORMAT = "ULCS-Execution-Manifest"
MANIFEST_VERSION = "0.5"
_CACHE_MODES = {"off", "read", "write", "read-write"}


class CacheError(RuntimeError):
    """Raised when a cache configuration or cache entry is invalid."""


class ManifestVerificationError(RuntimeError):
    """Raised when an execution manifest does not match a replay."""


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, set):
        return sorted((_jsonable(item) for item in value), key=canonical_json)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest_value(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def program_digest(program: Any) -> str:
    payload = {
        "format": "ULCS-Program-Definition",
        "version": MANIFEST_VERSION,
        "nodes": [
            {
                "node_id": node.node_id,
                "role": node.role,
                "language": node.language,
                "code": node.code,
                "inputs": [
                    {"node_id": ref.node_id, "field": ref.field, "alias": ref.alias}
                    for ref in node.inputs
                ],
            }
            for node in program.nodes
        ],
    }
    return digest_value(payload)


def plan_digest(program: Any) -> str:
    payload = {
        "format": "ULCS-Validated-Plan",
        "version": MANIFEST_VERSION,
        "nodes": [
            {
                "node_id": node.node_id,
                "language": node.language,
                "runtime": node.runtime,
                "input_type": node.input_type,
                "output_type": node.output_type,
                "effects": sorted(node.effects),
                "claims": sorted(claim.token for claim in node.claims),
                "taint_sources": sorted(node.taint_sources),
                "deterministic": bool(getattr(node, "deterministic", False)),
                "cacheable": bool(getattr(node, "cacheable", False)),
            }
            for node in program.topological_nodes()
        ],
        "execution_layers": [
            [node.node_id for node in layer] for layer in program.execution_layers()
        ],
    }
    return digest_value(payload)


def policy_digest(policy: Any | None) -> str:
    if policy is None:
        return digest_value({"mode": "none"})
    return digest_value(
        {
            "mode": policy.mode,
            "allow": list(policy.allow),
            "deny": list(policy.deny),
            "limits": policy.limits.to_dict(),
        }
    )


def node_fingerprint(node: Any, input_value: Any) -> tuple[str, str]:
    input_digest = digest_value(input_value)
    payload = {
        "format": "ULCS-Node-Execution-Key",
        "version": MANIFEST_VERSION,
        "node": {
            "node_id": node.node_id,
            "role": node.role,
            "language": node.language,
            "code": node.code,
            "runtime": node.runtime,
            "input_type": node.input_type,
            "output_type": node.output_type,
            "claims": sorted(claim.token for claim in node.claims),
            "deterministic": bool(getattr(node, "deterministic", False)),
        },
        "input_digest": input_digest,
    }
    return digest_value(payload), input_digest


@dataclass(frozen=True, slots=True)
class CacheConfig:
    mode: str = "off"
    directory: Path = Path(".ulcs-cache")

    def __post_init__(self) -> None:
        if self.mode not in _CACHE_MODES:
            raise CacheError(f"cache mode 必須是 {', '.join(sorted(_CACHE_MODES))}。")
        object.__setattr__(self, "directory", Path(self.directory))

    @property
    def can_read(self) -> bool:
        return self.mode in {"read", "read-write"}

    @property
    def can_write(self) -> bool:
        return self.mode in {"write", "read-write"}

    def summary(self) -> str:
        return f"mode={self.mode}; dir={self.directory}"


@dataclass(frozen=True, slots=True)
class CacheEntry:
    key: str
    output_digest: str
    value: Any


class ContentAddressedCache:
    def __init__(self, config: CacheConfig) -> None:
        self.config = config

    def path_for(self, key: str) -> Path:
        if len(key) != 64 or any(ch not in "0123456789abcdef" for ch in key):
            raise CacheError(f"非法 cache key：{key!r}")
        return self.config.directory / key[:2] / f"{key}.json"

    def load(self, key: str) -> CacheEntry | None:
        if not self.config.can_read:
            return None
        path = self.path_for(key)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        if (
            payload.get("format") != CACHE_FORMAT
            or payload.get("version") != CACHE_VERSION
            or payload.get("key") != key
            or "value" not in payload
        ):
            return None
        output_digest = str(payload.get("output_digest", ""))
        if output_digest != digest_value(payload["value"]):
            return None
        return CacheEntry(key=key, output_digest=output_digest, value=payload["value"])

    def store(self, key: str, value: Any) -> CacheEntry | None:
        if not self.config.can_write:
            return None
        normalized = json.loads(canonical_json(value))
        output_digest = digest_value(normalized)
        payload = {
            "format": CACHE_FORMAT,
            "version": CACHE_VERSION,
            "key": key,
            "output_digest": output_digest,
            "value": normalized,
        }
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{key}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, path)
        finally:
            try:
                Path(temp_name).unlink()
            except FileNotFoundError:
                pass
        return CacheEntry(key=key, output_digest=output_digest, value=normalized)


@dataclass(frozen=True, slots=True)
class ExecutionManifest:
    program_digest: str
    plan_digest: str
    policy_digest: str
    execution_layers: tuple[tuple[str, ...], ...]
    nodes: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": MANIFEST_FORMAT,
            "version": MANIFEST_VERSION,
            "program_digest": self.program_digest,
            "plan_digest": self.plan_digest,
            "policy_digest": self.policy_digest,
            "execution_layers": [list(layer) for layer in self.execution_layers],
            "nodes": self.nodes,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ExecutionManifest":
        if (
            payload.get("format") != MANIFEST_FORMAT
            or payload.get("version") != MANIFEST_VERSION
        ):
            raise ManifestVerificationError("manifest 格式或版本不相容。")
        nodes = payload.get("nodes")
        layers = payload.get("execution_layers")
        if not isinstance(nodes, dict) or not isinstance(layers, list):
            raise ManifestVerificationError("manifest 缺少 nodes 或 execution_layers。")
        try:
            normalized_nodes = {
                str(key): dict(value) for key, value in nodes.items()
            }
            normalized_layers = tuple(
                tuple(str(item) for item in layer) for layer in layers
            )
        except (TypeError, ValueError) as exc:
            raise ManifestVerificationError("manifest 節點或執行層格式錯誤。") from exc
        return cls(
            program_digest=str(payload.get("program_digest", "")),
            plan_digest=str(payload.get("plan_digest", "")),
            policy_digest=str(payload.get("policy_digest", "")),
            execution_layers=normalized_layers,
            nodes=normalized_nodes,
        )

    @classmethod
    def read(cls, path: str | Path) -> "ExecutionManifest":
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ManifestVerificationError(f"manifest 不是合法 JSON：{exc}") from exc
        if not isinstance(payload, dict):
            raise ManifestVerificationError("manifest 根節點必須是 object。")
        return cls.from_mapping(payload)


def verify_manifest(expected: ExecutionManifest, actual: ExecutionManifest) -> None:
    differences: list[str] = []
    for field in (
        "program_digest",
        "plan_digest",
        "policy_digest",
        "execution_layers",
    ):
        if getattr(expected, field) != getattr(actual, field):
            differences.append(field)
    expected_ids = tuple(expected.nodes)
    actual_ids = tuple(actual.nodes)
    if expected_ids != actual_ids:
        differences.append("node_order")
    for node_id in sorted(set(expected.nodes) & set(actual.nodes)):
        for field in (
            "fingerprint",
            "input_digest",
            "output_digest",
            "runtime",
            "taints",
        ):
            if expected.nodes[node_id].get(field) != actual.nodes[node_id].get(field):
                differences.append(f"nodes.{node_id}.{field}")
    if differences:
        rendered = ", ".join(dict.fromkeys(differences))
        raise ManifestVerificationError(f"執行重放與 manifest 不一致：{rendered}")
