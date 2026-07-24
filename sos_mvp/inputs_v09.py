from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .review import ReviewError, canonical_bytes, digest_mapping, sha256_file

INPUT_VERSION = "0.9"
INPUT_CONTRACT_FORMAT = "ULCS-Input-Contract"
INPUT_BUNDLE_FORMAT = "ULCS-Input-Bundle"
_DEFAULT_MAX_FILE_BYTES = 16 * 1024 * 1024
_DEFAULT_MAX_TOTAL_BYTES = 64 * 1024 * 1024
_ALLOWED_KINDS = {"file", "inline-text", "inline-json"}
_ALLOWED_CONTRACT_FIELDS = {"format", "version", "limits", "entries"}
_ALLOWED_ENTRY_FIELDS = {"name", "kind", "source", "mount", "media_type", "value"}


@dataclass(frozen=True, slots=True)
class InputLimits:
    max_file_bytes: int = _DEFAULT_MAX_FILE_BYTES
    max_total_bytes: int = _DEFAULT_MAX_TOTAL_BYTES

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "InputLimits":
        if payload is None:
            return cls()
        if not isinstance(payload, Mapping):
            raise ReviewError("Input Contract limits 必須是 JSON object。")
        unknown = sorted(set(payload).difference({"max_file_bytes", "max_total_bytes"}))
        if unknown:
            raise ReviewError("Input Contract limits 含未知欄位：" + ", ".join(unknown))
        max_file = _positive_int(payload.get("max_file_bytes", _DEFAULT_MAX_FILE_BYTES), "max_file_bytes")
        max_total = _positive_int(payload.get("max_total_bytes", _DEFAULT_MAX_TOTAL_BYTES), "max_total_bytes")
        if max_file > max_total:
            raise ReviewError("max_file_bytes 不可大於 max_total_bytes。")
        return cls(max_file_bytes=max_file, max_total_bytes=max_total)

    def to_dict(self) -> dict[str, int]:
        return {
            "max_file_bytes": self.max_file_bytes,
            "max_total_bytes": self.max_total_bytes,
        }


@dataclass(frozen=True, slots=True)
class InputEntry:
    name: str
    kind: str
    mount: str
    media_type: str
    source: str | None = None
    value: Any = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "InputEntry":
        if not isinstance(payload, Mapping):
            raise ReviewError("Input Contract entry 必須是 JSON object。")
        unknown = sorted(set(payload).difference(_ALLOWED_ENTRY_FIELDS))
        if unknown:
            raise ReviewError("Input Contract entry 含未知欄位：" + ", ".join(unknown))
        name = payload.get("name")
        kind = payload.get("kind")
        mount = payload.get("mount")
        media_type = payload.get("media_type", "application/octet-stream")
        if not isinstance(name, str) or not name.strip():
            raise ReviewError("Input entry name 不可為空。")
        normalized_name = name.strip()
        if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for ch in normalized_name):
            raise ReviewError(f"Input entry name 含不安全字元：{normalized_name!r}")
        if kind not in _ALLOWED_KINDS:
            raise ReviewError("Input entry kind 必須是 file、inline-text 或 inline-json。")
        if not isinstance(mount, str):
            raise ReviewError("Input entry mount 必須是字串。")
        normalized_mount = _safe_relative(mount, field="mount", require_inputs=True)
        if not isinstance(media_type, str) or not media_type.strip():
            raise ReviewError("Input entry media_type 不可為空。")

        source = payload.get("source")
        value = payload.get("value")
        if kind == "file":
            if not isinstance(source, str):
                raise ReviewError("file input 必須提供 source。")
            source = _safe_relative(source, field="source", require_inputs=False)
            if "value" in payload:
                raise ReviewError("file input 不得提供 value。")
        elif kind == "inline-text":
            if not isinstance(value, str):
                raise ReviewError("inline-text input 的 value 必須是字串。")
            if "source" in payload:
                raise ReviewError("inline-text input 不得提供 source。")
            source = None
        else:
            if "value" not in payload:
                raise ReviewError("inline-json input 必須提供 value。")
            _json_value_bytes(value)
            if "source" in payload:
                raise ReviewError("inline-json input 不得提供 source。")
            source = None

        return cls(
            name=normalized_name,
            kind=kind,
            mount=normalized_mount,
            media_type=media_type.strip(),
            source=source,
            value=value,
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "mount": self.mount,
            "media_type": self.media_type,
        }
        if self.kind == "file":
            payload["source"] = self.source
        else:
            payload["value"] = self.value
        return payload


@dataclass(frozen=True, slots=True)
class InputContract:
    root: Path
    limits: InputLimits
    entries: tuple[InputEntry, ...]
    digest: str

    @classmethod
    def read(cls, path: str | Path) -> "InputContract":
        contract_path = Path(path)
        try:
            payload = json.loads(contract_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ReviewError(f"Input Contract 不是合法 JSON：{exc}") from exc
        if not isinstance(payload, Mapping):
            raise ReviewError("Input Contract 根節點必須是 JSON object。")
        unknown = sorted(set(payload).difference(_ALLOWED_CONTRACT_FIELDS))
        if unknown:
            raise ReviewError("Input Contract 含未知欄位：" + ", ".join(unknown))
        if payload.get("format") != INPUT_CONTRACT_FORMAT:
            raise ReviewError("Input Contract format 不相容。")
        if str(payload.get("version")) != INPUT_VERSION:
            raise ReviewError("Input Contract version 不相容。")
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, Sequence) or isinstance(raw_entries, (str, bytes)) or not raw_entries:
            raise ReviewError("Input Contract entries 必須是非空陣列。")
        entries = tuple(InputEntry.from_mapping(item) for item in raw_entries)
        names = [entry.name for entry in entries]
        mounts = [entry.mount for entry in entries]
        if len(set(names)) != len(names):
            raise ReviewError("Input Contract entry name 不可重複。")
        if len(set(mounts)) != len(mounts):
            raise ReviewError("Input Contract mount 不可重複。")
        limits = InputLimits.from_mapping(payload.get("limits"))
        normalized = {
            "format": INPUT_CONTRACT_FORMAT,
            "version": INPUT_VERSION,
            "limits": limits.to_dict(),
            "entries": [entry.to_dict() for entry in entries],
        }
        return cls(
            root=contract_path.resolve().parent,
            limits=limits,
            entries=entries,
            digest=digest_mapping(normalized),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": INPUT_CONTRACT_FORMAT,
            "version": INPUT_VERSION,
            "limits": self.limits.to_dict(),
            "entries": [entry.to_dict() for entry in self.entries],
        }

    def capture(self, directory: str | Path) -> "InputBundle":
        target = Path(directory).resolve()
        target.mkdir(parents=True, exist_ok=True)
        _reject_symlink_path(target, target)
        total = 0
        captured: list[dict[str, Any]] = []
        _atomic_write_json(target / "input-contract.json", self.to_dict())
        for entry in self.entries:
            data = self._read_entry(entry)
            size = len(data)
            if size > self.limits.max_file_bytes:
                raise ReviewError(
                    f"Input {entry.name} 超過 max_file_bytes：{size} > {self.limits.max_file_bytes}"
                )
            total += size
            if total > self.limits.max_total_bytes:
                raise ReviewError(
                    f"Input Bundle 超過 max_total_bytes：{total} > {self.limits.max_total_bytes}"
                )
            destination = _destination_path(target, entry.mount)
            _atomic_write_bytes(destination, data)
            captured.append(
                {
                    "name": entry.name,
                    "kind": entry.kind,
                    "mount": entry.mount,
                    "media_type": entry.media_type,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size": size,
                }
            )
        unsigned = {
            "format": INPUT_BUNDLE_FORMAT,
            "version": INPUT_VERSION,
            "contract_digest": self.digest,
            "entries": captured,
        }
        bundle = InputBundle(
            root=target,
            contract_digest=self.digest,
            entries=tuple(captured),
            digest=digest_mapping(unsigned),
        )
        _atomic_write_json(target / "input-bundle.json", bundle.to_dict())
        return bundle

    def _read_entry(self, entry: InputEntry) -> bytes:
        if entry.kind == "inline-text":
            return str(entry.value).encode("utf-8")
        if entry.kind == "inline-json":
            return _json_value_bytes(entry.value)
        assert entry.source is not None
        source = (self.root / Path(entry.source)).resolve()
        if not source.is_relative_to(self.root):
            raise ReviewError(f"Input source 超出 Contract 根目錄：{entry.source}")
        _reject_symlink_path(self.root, source)
        if not source.is_file():
            raise ReviewError(f"Input source 不存在或不是檔案：{entry.source}")
        return source.read_bytes()


@dataclass(frozen=True, slots=True)
class InputBundle:
    root: Path
    contract_digest: str
    entries: tuple[Mapping[str, Any], ...]
    digest: str

    @classmethod
    def read(cls, path: str | Path) -> "InputBundle":
        candidate = Path(path)
        manifest = candidate / "input-bundle.json" if candidate.is_dir() else candidate
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ReviewError(f"Input Bundle 不是合法 JSON：{exc}") from exc
        if not isinstance(payload, Mapping):
            raise ReviewError("Input Bundle 根節點必須是 JSON object。")
        if set(payload) != {"format", "version", "contract_digest", "entries", "digest"}:
            raise ReviewError("Input Bundle 欄位集合不符合 v0.9。")
        if payload.get("format") != INPUT_BUNDLE_FORMAT or str(payload.get("version")) != INPUT_VERSION:
            raise ReviewError("Input Bundle format 或 version 不相容。")
        contract_digest = payload.get("contract_digest")
        digest = payload.get("digest")
        if not _is_sha256(contract_digest) or not _is_sha256(digest):
            raise ReviewError("Input Bundle digest 欄位無效。")
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, list) or not raw_entries:
            raise ReviewError("Input Bundle entries 必須是非空陣列。")
        entries = tuple(_normalize_bundle_entry(item) for item in raw_entries)
        names = [str(item["name"]) for item in entries]
        mounts = [str(item["mount"]) for item in entries]
        if len(set(names)) != len(names) or len(set(mounts)) != len(mounts):
            raise ReviewError("Input Bundle name 或 mount 不可重複。")
        unsigned = {
            "format": INPUT_BUNDLE_FORMAT,
            "version": INPUT_VERSION,
            "contract_digest": contract_digest,
            "entries": [dict(item) for item in entries],
        }
        expected = digest_mapping(unsigned)
        if not hmac.compare_digest(digest, expected):
            raise ReviewError("Input Bundle canonical digest 不一致。")
        bundle = cls(
            root=manifest.resolve().parent,
            contract_digest=contract_digest,
            entries=entries,
            digest=digest,
        )
        bundle.verify()
        return bundle

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": INPUT_BUNDLE_FORMAT,
            "version": INPUT_VERSION,
            "contract_digest": self.contract_digest,
            "entries": [dict(item) for item in self.entries],
            "digest": self.digest,
        }

    def verify(self) -> None:
        contract_path = self.root / "input-contract.json"
        if contract_path.is_symlink() or not contract_path.is_file():
            raise ReviewError("Input Bundle 缺少安全的 input-contract.json。")
        contract = InputContract.read(contract_path)
        if not hmac.compare_digest(contract.digest, self.contract_digest):
            raise ReviewError("Input Contract canonical digest 已變更。")
        contract_entries = {entry.name: entry for entry in contract.entries}
        if set(contract_entries) != {str(item["name"]) for item in self.entries}:
            raise ReviewError("Input Bundle 與 Input Contract entry 集合不一致。")
        total = 0
        for metadata in self.entries:
            entry = contract_entries[str(metadata["name"])]
            if entry.kind != metadata["kind"] or entry.mount != metadata["mount"]:
                raise ReviewError(f"Input Bundle metadata 與 Contract 不一致：{entry.name}")
            path = _destination_path(self.root, str(metadata["mount"]))
            if path.is_symlink() or not path.is_file():
                raise ReviewError(f"Input Bundle 檔案遺失或為符號連結：{entry.mount}")
            size = path.stat().st_size
            if size != metadata["size"]:
                raise ReviewError(f"Input Bundle 檔案大小已變更：{entry.mount}")
            if not hmac.compare_digest(sha256_file(path), str(metadata["sha256"])):
                raise ReviewError(f"Input Bundle 檔案摘要已變更：{entry.mount}")
            if size > contract.limits.max_file_bytes:
                raise ReviewError(f"Input Bundle 檔案超過 Contract 限制：{entry.mount}")
            total += size
        if total > contract.limits.max_total_bytes:
            raise ReviewError("Input Bundle 總大小超過 Contract 限制。")

    def copy_to(self, directory: str | Path) -> "InputBundle":
        self.verify()
        target = Path(directory).resolve()
        target.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(
            target / "input-contract.json",
            (self.root / "input-contract.json").read_bytes(),
        )
        for metadata in self.entries:
            source = _destination_path(self.root, str(metadata["mount"]))
            destination = _destination_path(target, str(metadata["mount"]))
            _atomic_write_bytes(destination, source.read_bytes())
        _atomic_write_json(target / "input-bundle.json", self.to_dict())
        copied = InputBundle.read(target / "input-bundle.json")
        if not hmac.compare_digest(copied.digest, self.digest):
            raise ReviewError("Input Bundle 快照 digest 不一致。")
        return copied


def _normalize_bundle_entry(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ReviewError("Input Bundle entry 必須是 JSON object。")
    required = {"name", "kind", "mount", "media_type", "sha256", "size"}
    if set(payload) != required:
        raise ReviewError("Input Bundle entry 欄位集合無效。")
    name = payload.get("name")
    kind = payload.get("kind")
    mount = payload.get("mount")
    media_type = payload.get("media_type")
    digest = payload.get("sha256")
    size = payload.get("size")
    if not isinstance(name, str) or not name:
        raise ReviewError("Input Bundle entry name 無效。")
    if kind not in _ALLOWED_KINDS:
        raise ReviewError("Input Bundle entry kind 無效。")
    if not isinstance(mount, str):
        raise ReviewError("Input Bundle entry mount 無效。")
    normalized_mount = _safe_relative(mount, field="mount", require_inputs=True)
    if not isinstance(media_type, str) or not media_type:
        raise ReviewError("Input Bundle media_type 無效。")
    if not _is_sha256(digest):
        raise ReviewError("Input Bundle entry sha256 無效。")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ReviewError("Input Bundle entry size 無效。")
    return {
        "name": name,
        "kind": kind,
        "mount": normalized_mount,
        "media_type": media_type,
        "sha256": digest,
        "size": size,
    }


def _safe_relative(value: str, *, field: str, require_inputs: bool) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    parts = path.parts
    if not parts or path.is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise ReviewError(f"Input {field} 必須是不可穿越的相對路徑：{value!r}")
    if any(":" in part for part in parts):
        raise ReviewError(f"Input {field} 不接受磁碟代號或 URI：{value!r}")
    if require_inputs and parts[0] != "inputs":
        raise ReviewError("Input mount 必須位於 inputs/ 之下。")
    return "/".join(parts)


def _destination_path(root: Path, relative: str) -> Path:
    normalized = _safe_relative(relative, field="mount", require_inputs=True)
    destination = (root / Path(normalized)).resolve(strict=False)
    if not destination.is_relative_to(root.resolve()):
        raise ReviewError(f"Input mount 超出 Bundle 根目錄：{relative}")
    _reject_symlink_path(root.resolve(), destination)
    return destination


def _reject_symlink_path(root: Path, target: Path) -> None:
    root = root.resolve()
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ReviewError(f"路徑超出允許根目錄：{target}") from exc
    current = root
    if current.is_symlink():
        raise ReviewError(f"根目錄不得為符號連結：{root}")
    for part in relative.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ReviewError(f"Input 路徑不得穿過符號連結：{current}")


def _json_value_bytes(value: Any) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ReviewError(f"inline-json value 無法 canonicalize：{exc}") from exc
    return encoded.encode("utf-8")


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ReviewError(f"{field} 必須是正整數。")
    return value


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        ch in "0123456789abcdef" for ch in value
    )


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    content = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    _atomic_write_bytes(path, content.encode("utf-8"))


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            Path(temp_name).unlink()
        except FileNotFoundError:
            pass
