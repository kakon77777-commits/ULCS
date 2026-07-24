from __future__ import annotations

import hmac
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .review import ReviewError, digest_mapping
from .v09_crypto import ALGORITHM, public_key_id, sign_mapping, verify_mapping

TRANSPARENCY_VERSION = "0.9"
TRANSPARENCY_LOG_FORMAT = "ULCS-Transparency-Log"
TRANSPARENCY_CHECKPOINT_FORMAT = "ULCS-Transparency-Checkpoint"


@dataclass(frozen=True, slots=True)
class TransparencyEntry:
    sequence: int
    event: str
    subject: str
    issued_at: str
    previous_digest: str | None
    metadata: Mapping[str, Any]
    digest: str

    @classmethod
    def build(
        cls,
        *,
        sequence: int,
        event: str,
        subject: str,
        previous_digest: str | None,
        metadata: Mapping[str, Any] | None = None,
        issued_at: str | None = None,
    ) -> "TransparencyEntry":
        normalized_event = event.strip()
        normalized_subject = subject.strip()
        if not normalized_event or any(ch.isspace() for ch in normalized_event):
            raise ReviewError("Transparency event 必須是無空白的非空字串。")
        if not normalized_subject:
            raise ReviewError("Transparency subject 不可為空。")
        if isinstance(sequence, bool) or sequence < 0:
            raise ReviewError("Transparency sequence 無效。")
        if previous_digest is not None and not _is_sha256(previous_digest):
            raise ReviewError("Transparency previous_digest 無效。")
        normalized_metadata = dict(metadata or {})
        timestamp = issued_at or _now()
        _validate_timestamp(timestamp)
        unsigned = _entry_unsigned(
            sequence=sequence,
            event=normalized_event,
            subject=normalized_subject,
            issued_at=timestamp,
            previous_digest=previous_digest,
            metadata=normalized_metadata,
        )
        return cls(
            sequence=sequence,
            event=normalized_event,
            subject=normalized_subject,
            issued_at=timestamp,
            previous_digest=previous_digest,
            metadata=normalized_metadata,
            digest=digest_mapping(unsigned),
        )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "TransparencyEntry":
        required = {"sequence", "event", "subject", "issued_at", "previous_digest", "metadata", "digest"}
        if not isinstance(payload, Mapping) or set(payload) != required:
            raise ReviewError("Transparency entry 欄位集合無效。")
        sequence = payload.get("sequence")
        event = payload.get("event")
        subject = payload.get("subject")
        issued_at = payload.get("issued_at")
        previous = payload.get("previous_digest")
        metadata = payload.get("metadata")
        digest = payload.get("digest")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ReviewError("Transparency entry sequence 無效。")
        if not isinstance(event, str) or not event or any(ch.isspace() for ch in event):
            raise ReviewError("Transparency entry event 無效。")
        if not isinstance(subject, str) or not subject:
            raise ReviewError("Transparency entry subject 無效。")
        if not isinstance(issued_at, str):
            raise ReviewError("Transparency entry issued_at 無效。")
        _validate_timestamp(issued_at)
        if previous is not None and not _is_sha256(previous):
            raise ReviewError("Transparency entry previous_digest 無效。")
        if not isinstance(metadata, Mapping):
            raise ReviewError("Transparency entry metadata 必須是 JSON object。")
        if not _is_sha256(digest):
            raise ReviewError("Transparency entry digest 無效。")
        entry = cls(
            sequence=sequence,
            event=event,
            subject=subject,
            issued_at=issued_at,
            previous_digest=previous,
            metadata=dict(metadata),
            digest=digest,
        )
        expected = digest_mapping(entry.unsigned_dict())
        if not hmac.compare_digest(expected, digest):
            raise ReviewError(f"Transparency entry digest 不一致：sequence={sequence}")
        return entry

    def unsigned_dict(self) -> dict[str, Any]:
        return _entry_unsigned(
            sequence=self.sequence,
            event=self.event,
            subject=self.subject,
            issued_at=self.issued_at,
            previous_digest=self.previous_digest,
            metadata=self.metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = self.unsigned_dict()
        payload["digest"] = self.digest
        return payload


@dataclass(frozen=True, slots=True)
class TransparencyLog:
    path: Path
    entries: tuple[TransparencyEntry, ...]
    head_digest: str | None

    @classmethod
    def read(cls, path: str | Path, *, create: bool = False) -> "TransparencyLog":
        log_path = Path(path)
        if not log_path.exists():
            if not create:
                raise ReviewError(f"Transparency Log 不存在：{log_path}")
            log = cls(path=log_path.resolve(), entries=(), head_digest=None)
            log.write()
            return log
        try:
            payload = json.loads(log_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ReviewError(f"Transparency Log 不是合法 JSON：{exc}") from exc
        if not isinstance(payload, Mapping) or set(payload) != {"format", "version", "entries", "head_digest"}:
            raise ReviewError("Transparency Log 欄位集合不符合 v0.9。")
        if payload.get("format") != TRANSPARENCY_LOG_FORMAT:
            raise ReviewError("Transparency Log format 不相容。")
        if str(payload.get("version")) != TRANSPARENCY_VERSION:
            raise ReviewError("Transparency Log version 不相容。")
        raw_entries = payload.get("entries")
        head = payload.get("head_digest")
        if not isinstance(raw_entries, Sequence) or isinstance(raw_entries, (str, bytes)):
            raise ReviewError("Transparency Log entries 必須是陣列。")
        entries = tuple(TransparencyEntry.from_mapping(item) for item in raw_entries)
        if head is not None and not _is_sha256(head):
            raise ReviewError("Transparency Log head_digest 無效。")
        log = cls(path=log_path.resolve(), entries=entries, head_digest=head)
        log.verify()
        return log

    def verify(self, *, expected_head: str | None = None) -> None:
        previous: str | None = None
        for index, entry in enumerate(self.entries):
            if entry.sequence != index:
                raise ReviewError("Transparency Log sequence 不連續。")
            if entry.previous_digest != previous:
                raise ReviewError(f"Transparency Log hash chain 中斷：sequence={index}")
            expected = digest_mapping(entry.unsigned_dict())
            if not hmac.compare_digest(expected, entry.digest):
                raise ReviewError(f"Transparency Log entry digest 不一致：sequence={index}")
            previous = entry.digest
        if previous != self.head_digest:
            raise ReviewError("Transparency Log head_digest 與最後 entry 不一致。")
        if expected_head is not None:
            if not _is_sha256(expected_head):
                raise ReviewError("expected transparency head 必須是 SHA-256。")
            if not hmac.compare_digest(expected_head, self.head_digest or ""):
                raise ReviewError("Transparency Log head 與可信 checkpoint 不一致。")

    def append(
        self,
        *,
        event: str,
        subject: str,
        metadata: Mapping[str, Any] | None = None,
        expected_head: str | None = None,
    ) -> "TransparencyLog":
        self.verify(expected_head=expected_head)
        entry = TransparencyEntry.build(
            sequence=len(self.entries),
            event=event,
            subject=subject,
            previous_digest=self.head_digest,
            metadata=metadata,
        )
        updated = TransparencyLog(
            path=self.path,
            entries=(*self.entries, entry),
            head_digest=entry.digest,
        )
        updated.write()
        return updated

    def contains(self, *, event: str, subject: str) -> bool:
        return any(entry.event == event and entry.subject == subject for entry in self.entries)

    def key_revoked(self, key_id: str) -> bool:
        return self.contains(event="key-revoked", subject=key_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": TRANSPARENCY_LOG_FORMAT,
            "version": TRANSPARENCY_VERSION,
            "entries": [entry.to_dict() for entry in self.entries],
            "head_digest": self.head_digest,
        }

    def write(self) -> None:
        _atomic_write_json(self.path, self.to_dict())


@dataclass(frozen=True, slots=True)
class TransparencyCheckpoint:
    log_head: str
    entry_count: int
    signer_id: str
    key_id: str
    issued_at: str
    signature: str

    @classmethod
    def create(
        cls,
        log: TransparencyLog,
        private_key: Ed25519PrivateKey,
        *,
        signer: str,
        issued_at: str | None = None,
    ) -> "TransparencyCheckpoint":
        log.verify()
        if log.head_digest is None:
            raise ReviewError("空的 Transparency Log 無法建立 checkpoint。")
        signer_id = signer.strip()
        if not signer_id:
            raise ReviewError("Transparency checkpoint signer 不可為空。")
        timestamp = issued_at or _now()
        _validate_timestamp(timestamp)
        key_id = public_key_id(private_key.public_key())
        unsigned = _checkpoint_unsigned(
            log_head=log.head_digest,
            entry_count=len(log.entries),
            signer_id=signer_id,
            key_id=key_id,
            issued_at=timestamp,
        )
        return cls(
            log_head=log.head_digest,
            entry_count=len(log.entries),
            signer_id=signer_id,
            key_id=key_id,
            issued_at=timestamp,
            signature=sign_mapping(private_key, unsigned),
        )

    @classmethod
    def read(cls, path: str | Path) -> "TransparencyCheckpoint":
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ReviewError(f"Transparency Checkpoint 不是合法 JSON：{exc}") from exc
        required = {"format", "version", "algorithm", "log_head", "entry_count", "signer", "issued_at", "signature"}
        if not isinstance(payload, Mapping) or set(payload) != required:
            raise ReviewError("Transparency Checkpoint 欄位集合不符合 v0.9。")
        if payload.get("format") != TRANSPARENCY_CHECKPOINT_FORMAT:
            raise ReviewError("Transparency Checkpoint format 不相容。")
        if str(payload.get("version")) != TRANSPARENCY_VERSION or payload.get("algorithm") != ALGORITHM:
            raise ReviewError("Transparency Checkpoint version 或 algorithm 不相容。")
        log_head = payload.get("log_head")
        entry_count = payload.get("entry_count")
        signer = payload.get("signer")
        issued_at = payload.get("issued_at")
        signature = payload.get("signature")
        if not _is_sha256(log_head):
            raise ReviewError("Transparency Checkpoint log_head 無效。")
        if isinstance(entry_count, bool) or not isinstance(entry_count, int) or entry_count <= 0:
            raise ReviewError("Transparency Checkpoint entry_count 無效。")
        if not isinstance(signer, Mapping) or set(signer) != {"id", "key_id"}:
            raise ReviewError("Transparency Checkpoint signer metadata 無效。")
        signer_id = signer.get("id")
        key_id = signer.get("key_id")
        if not isinstance(signer_id, str) or not signer_id.strip() or not isinstance(key_id, str):
            raise ReviewError("Transparency Checkpoint signer 欄位無效。")
        _validate_key_id(key_id)
        if not isinstance(issued_at, str) or not isinstance(signature, str):
            raise ReviewError("Transparency Checkpoint issued_at 或 signature 無效。")
        _validate_timestamp(issued_at)
        return cls(
            log_head=log_head,
            entry_count=entry_count,
            signer_id=signer_id.strip(),
            key_id=key_id,
            issued_at=issued_at,
            signature=signature,
        )

    def unsigned_dict(self) -> dict[str, Any]:
        return _checkpoint_unsigned(
            log_head=self.log_head,
            entry_count=self.entry_count,
            signer_id=self.signer_id,
            key_id=self.key_id,
            issued_at=self.issued_at,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = self.unsigned_dict()
        payload["signature"] = self.signature
        return payload

    def verify(self, log: TransparencyLog, public_key: Ed25519PublicKey) -> None:
        log.verify(expected_head=self.log_head)
        if len(log.entries) != self.entry_count:
            raise ReviewError("Transparency Checkpoint entry_count 與 Log 不一致。")
        actual_key_id = public_key_id(public_key)
        if not hmac.compare_digest(self.key_id, actual_key_id):
            raise ReviewError("Transparency Checkpoint key_id 與 public key 不一致。")
        verify_mapping(public_key, self.unsigned_dict(), self.signature)

    def write(self, path: str | Path) -> None:
        _atomic_write_json(Path(path), self.to_dict())


def _entry_unsigned(
    *,
    sequence: int,
    event: str,
    subject: str,
    issued_at: str,
    previous_digest: str | None,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "event": event,
        "subject": subject,
        "issued_at": issued_at,
        "previous_digest": previous_digest,
        "metadata": dict(metadata),
    }


def _checkpoint_unsigned(
    *,
    log_head: str,
    entry_count: int,
    signer_id: str,
    key_id: str,
    issued_at: str,
) -> dict[str, Any]:
    return {
        "format": TRANSPARENCY_CHECKPOINT_FORMAT,
        "version": TRANSPARENCY_VERSION,
        "algorithm": ALGORITHM,
        "log_head": log_head,
        "entry_count": entry_count,
        "signer": {"id": signer_id, "key_id": key_id},
        "issued_at": issued_at,
    }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_timestamp(value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewError("Transparency timestamp 不是合法 ISO-8601。") from exc
    if parsed.tzinfo is None:
        raise ReviewError("Transparency timestamp 必須包含 timezone。")


def _validate_key_id(value: str) -> None:
    if not value.startswith("sha256:") or not _is_sha256(value[7:]):
        raise ReviewError("Transparency key_id 必須是 sha256:<64 hex>。")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        ch in "0123456789abcdef" for ch in value
    )


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    content = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            Path(temp_name).unlink()
        except FileNotFoundError:
            pass
