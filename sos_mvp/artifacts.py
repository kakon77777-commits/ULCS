from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .provenance import canonical_json, digest_value

ARTIFACT_FORMAT = "ULCS-Artifact"
ARTIFACT_VERSION = "0.6"
CONTRACT_FORMAT = "ULCS-Artifact-Contract"
CONTRACT_VERSION = "0.6"
CHECKPOINT_FORMAT = "ULCS-Execution-Checkpoint"
CHECKPOINT_VERSION = "0.6"


class ArtifactError(RuntimeError):
    """Raised when an artifact cannot be stored or verified."""


class SchemaValidationError(ValueError):
    """Raised when a value violates an Artifact Contract schema."""


class CheckpointError(RuntimeError):
    """Raised when a checkpoint is invalid or incompatible."""


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            Path(temp_name).unlink()
        except FileNotFoundError:
            pass


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise SchemaValidationError(f"不支援的 schema type：{expected}")


def validate_schema(value: Any, schema: Mapping[str, Any] | None, *, path: str = "$") -> None:
    """Validate the deterministic JSON Schema subset used by ULCS v0.6."""
    if schema is None:
        return
    if not isinstance(schema, Mapping):
        raise SchemaValidationError(f"{path}：schema 必須是 object。")

    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(_type_matches(value, str(item)) for item in expected):
            raise SchemaValidationError(f"{path}：值不符合允許型別 {expected}。")
    elif expected is not None and not _type_matches(value, str(expected)):
        raise SchemaValidationError(f"{path}：預期 {expected}，實際為 {type(value).__name__}。")

    if "enum" in schema and value not in schema["enum"]:
        raise SchemaValidationError(f"{path}：值不在 enum 中。")

    if isinstance(value, Mapping):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, Mapping) or not isinstance(required, list):
            raise SchemaValidationError(f"{path}：properties／required 格式錯誤。")
        for key in required:
            if key not in value:
                raise SchemaValidationError(f"{path}：缺少必要欄位 {key}。")
        for key, item in value.items():
            if key in properties:
                validate_schema(item, properties[key], path=f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                raise SchemaValidationError(f"{path}：不允許額外欄位 {key}。")

    if isinstance(value, list) and "items" in schema:
        item_schema = schema["items"]
        for index, item in enumerate(value):
            validate_schema(item, item_schema, path=f"{path}[{index}]")

    if isinstance(value, (str, list, Mapping)):
        length = len(value)
        minimum = schema.get("minLength", schema.get("minItems", schema.get("minProperties")))
        maximum = schema.get("maxLength", schema.get("maxItems", schema.get("maxProperties")))
        if minimum is not None and length < int(minimum):
            raise SchemaValidationError(f"{path}：長度 {length} 小於下限 {minimum}。")
        if maximum is not None and length > int(maximum):
            raise SchemaValidationError(f"{path}：長度 {length} 超過上限 {maximum}。")


@dataclass(frozen=True, slots=True)
class NodeContract:
    input_schema: Mapping[str, Any] | None = None
    output_schema: Mapping[str, Any] | None = None
    persist: bool = False


@dataclass(frozen=True, slots=True)
class ArtifactContracts:
    nodes: Mapping[str, NodeContract] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ArtifactContracts":
        if payload.get("format") != CONTRACT_FORMAT or payload.get("version") != CONTRACT_VERSION:
            raise ArtifactError("Artifact Contract 格式或版本不相容。")
        raw_nodes = payload.get("nodes", {})
        if not isinstance(raw_nodes, Mapping):
            raise ArtifactError("Artifact Contract 的 nodes 必須是 object。")
        nodes: dict[str, NodeContract] = {}
        for node_id, raw in raw_nodes.items():
            if not isinstance(raw, Mapping):
                raise ArtifactError(f"節點 {node_id} 的 contract 必須是 object。")
            input_schema = raw.get("input_schema")
            output_schema = raw.get("output_schema")
            if input_schema is not None and not isinstance(input_schema, Mapping):
                raise ArtifactError(f"節點 {node_id} 的 input_schema 必須是 object。")
            if output_schema is not None and not isinstance(output_schema, Mapping):
                raise ArtifactError(f"節點 {node_id} 的 output_schema 必須是 object。")
            nodes[str(node_id)] = NodeContract(
                input_schema=input_schema,
                output_schema=output_schema,
                persist=bool(raw.get("persist", False)),
            )
        return cls(nodes=nodes)

    @classmethod
    def read(cls, path: str | Path) -> "ArtifactContracts":
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ArtifactError(f"Artifact Contract 不是合法 JSON：{exc}") from exc
        if not isinstance(payload, Mapping):
            raise ArtifactError("Artifact Contract 根節點必須是 object。")
        return cls.from_mapping(payload)

    def apply(self, program: Any) -> None:
        by_id = program.node_map()
        unknown = sorted(set(self.nodes) - set(by_id))
        if unknown:
            raise ArtifactError(f"Artifact Contract 引用了不存在的節點：{', '.join(unknown)}")
        for node_id, contract in self.nodes.items():
            node = by_id[node_id]
            node.input_schema = dict(contract.input_schema) if contract.input_schema is not None else None
            node.output_schema = dict(contract.output_schema) if contract.output_schema is not None else None
            node.persist_output = contract.persist


@dataclass(frozen=True, slots=True)
class ArtifactConfig:
    directory: Path = Path(".ulcs-artifacts")
    threshold_bytes: int = 262_144
    persist_all: bool = False

    def __post_init__(self) -> None:
        if self.threshold_bytes < 0:
            raise ArtifactError("artifact threshold 不可小於 0。")
        object.__setattr__(self, "directory", Path(self.directory))

    def summary(self) -> str:
        return (
            f"dir={self.directory}; threshold={self.threshold_bytes}B; "
            f"persist_all={self.persist_all}"
        )


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    digest: str
    media_type: str
    encoding: str
    size: int
    path: str
    schema_digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": ARTIFACT_FORMAT,
            "version": ARTIFACT_VERSION,
            "digest": self.digest,
            "media_type": self.media_type,
            "encoding": self.encoding,
            "size": self.size,
            "path": self.path,
            "schema_digest": self.schema_digest,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ArtifactRef":
        if payload.get("format") != ARTIFACT_FORMAT or payload.get("version") != ARTIFACT_VERSION:
            raise ArtifactError("Artifact Reference 格式或版本不相容。")
        digest = str(payload.get("digest", ""))
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ArtifactError("Artifact digest 非法。")
        size = int(payload.get("size", -1))
        path = str(payload.get("path", ""))
        if size < 0 or not path:
            raise ArtifactError("Artifact size 或 path 非法。")
        return cls(
            digest=digest,
            media_type=str(payload.get("media_type", "application/json")),
            encoding=str(payload.get("encoding", "utf-8")),
            size=size,
            path=path,
            schema_digest=(
                str(payload["schema_digest"]) if payload.get("schema_digest") is not None else None
            ),
        )


class ArtifactStore:
    def __init__(self, config: ArtifactConfig) -> None:
        self.config = config

    def _object_path(self, digest: str, schema_digest: str | None) -> Path:
        contract_key = schema_digest or "no-schema"
        return (
            self.config.directory
            / "objects"
            / digest[:2]
            / f"{digest}.{contract_key}.json"
        )

    def _resolve_ref_path(self, ref: ArtifactRef) -> Path:
        relative = Path(ref.path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ArtifactError("Artifact path 必須位於 Artifact Store 內。")
        root = self.config.directory.resolve()
        resolved = (root / relative).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ArtifactError("Artifact path 逃出 Artifact Store。") from exc
        return resolved

    def should_persist(self, *, size: int, explicit: bool = False) -> bool:
        return self.config.persist_all or explicit or size >= self.config.threshold_bytes

    def store(self, value: Any, schema: Mapping[str, Any] | None = None) -> ArtifactRef:
        validate_schema(value, schema)
        canonical = canonical_json(value)
        encoded = canonical.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        schema_digest = digest_value(schema) if schema is not None else None
        path = self._object_path(digest, schema_digest)
        relative = path.relative_to(self.config.directory).as_posix()
        ref = ArtifactRef(
            digest=digest,
            media_type="application/json",
            encoding="utf-8",
            size=len(encoded),
            path=relative,
            schema_digest=schema_digest,
        )
        payload = {**ref.to_dict(), "value": json.loads(canonical)}
        if path.exists():
            try:
                self.load(ref, schema)
                return ref
            except ArtifactError:
                pass
        _atomic_write_json(path, payload)
        return ref

    def load(self, ref: ArtifactRef, schema: Mapping[str, Any] | None = None) -> Any:
        path = self._resolve_ref_path(ref)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ArtifactError(f"Artifact 不存在：{path}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactError(f"Artifact 無法讀取：{path}") from exc
        if not isinstance(payload, Mapping):
            raise ArtifactError("Artifact 根節點必須是 object。")
        stored = ArtifactRef.from_mapping(payload)
        if stored != ref:
            raise ArtifactError("Artifact Reference 與儲存內容不一致。")
        value = payload.get("value")
        encoded = canonical_json(value).encode("utf-8")
        if hashlib.sha256(encoded).hexdigest() != ref.digest or len(encoded) != ref.size:
            raise ArtifactError("Artifact 內容摘要或大小驗證失敗。")
        expected_schema_digest = digest_value(schema) if schema is not None else None
        if ref.schema_digest != expected_schema_digest:
            raise ArtifactError("Artifact schema digest 與目前契約不一致。")
        validate_schema(value, schema)
        return value


@dataclass(frozen=True, slots=True)
class ExecutionCheckpoint:
    program_digest: str
    plan_digest: str
    policy_digest: str
    nodes: Mapping[str, Mapping[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": CHECKPOINT_FORMAT,
            "version": CHECKPOINT_VERSION,
            "program_digest": self.program_digest,
            "plan_digest": self.plan_digest,
            "policy_digest": self.policy_digest,
            "nodes": dict(self.nodes),
        }

    def write(self, path: str | Path) -> None:
        _atomic_write_json(Path(path), self.to_dict())

    @classmethod
    def read(cls, path: str | Path) -> "ExecutionCheckpoint":
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise CheckpointError(f"checkpoint 不存在：{path}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise CheckpointError(f"checkpoint 無法讀取：{path}") from exc
        if not isinstance(payload, Mapping):
            raise CheckpointError("checkpoint 根節點必須是 object。")
        if payload.get("format") != CHECKPOINT_FORMAT or payload.get("version") != CHECKPOINT_VERSION:
            raise CheckpointError("checkpoint 格式或版本不相容。")
        nodes = payload.get("nodes")
        if not isinstance(nodes, Mapping):
            raise CheckpointError("checkpoint 缺少 nodes。")
        return cls(
            program_digest=str(payload.get("program_digest", "")),
            plan_digest=str(payload.get("plan_digest", "")),
            policy_digest=str(payload.get("policy_digest", "")),
            nodes={str(key): dict(value) for key, value in nodes.items()},
        )

    def verify_plan(self, program_digest_value: str, plan_digest_value: str, policy_digest_value: str) -> None:
        differences = []
        if self.program_digest != program_digest_value:
            differences.append("program_digest")
        if self.plan_digest != plan_digest_value:
            differences.append("plan_digest")
        if self.policy_digest != policy_digest_value:
            differences.append("policy_digest")
        if differences:
            raise CheckpointError("checkpoint 與目前計畫不相容：" + ", ".join(differences))
