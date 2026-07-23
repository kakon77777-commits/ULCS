from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .intent import IntentBundle, IntentCompileError, IntentRequest, compile_intent

REVIEW_VERSION = "0.8"
PROVIDER_PROPOSAL_FORMAT = "ULCS-Intent-Provider-Proposal"
REVIEW_BUNDLE_FORMAT = "ULCS-Review-Bundle"
APPROVAL_FORMAT = "ULCS-Approval-Record"
APPROVAL_ALGORITHM = "hmac-sha256"

_GENERATED_FILES = (
    "provider-proposal.json",
    "intent-plan.json",
    "workflow.sos",
    "artifact-contract.json",
    "capability-policy.json",
    "intent-bundle.json",
)
_FORBIDDEN_PROPOSAL_FIELDS = {
    "approval",
    "claims",
    "policy",
    "ready",
    "signature",
    "status",
    "workflow",
}


class ReviewError(ValueError):
    """Raised when a provider proposal, review bundle, or approval is invalid."""


@dataclass(frozen=True, slots=True)
class ProviderProposal:
    provider_id: str
    model: str
    request: IntentRequest
    notes: tuple[str, ...] = ()
    confidence: float | None = None
    source: str = "mapping"

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
        *,
        source: str = "mapping",
    ) -> "ProviderProposal":
        if not isinstance(payload, Mapping):
            raise ReviewError("Provider Proposal 根節點必須是 JSON object。")
        if payload.get("format") not in {None, PROVIDER_PROPOSAL_FORMAT}:
            raise ReviewError("Provider Proposal format 不相容。")
        if payload.get("version") is not None and str(payload["version"]) != REVIEW_VERSION:
            raise ReviewError("Provider Proposal version 不相容。")

        forbidden = sorted(_FORBIDDEN_PROPOSAL_FIELDS.intersection(payload))
        if forbidden:
            raise ReviewError(
                "Provider Proposal 不得自行提供治理或執行欄位："
                + ", ".join(forbidden)
            )
        allowed = {"format", "version", "provider", "request", "notes", "confidence"}
        unknown = sorted(set(payload).difference(allowed))
        if unknown:
            raise ReviewError("Provider Proposal 含未知欄位：" + ", ".join(unknown))

        provider = payload.get("provider")
        if not isinstance(provider, Mapping):
            raise ReviewError("provider 必須是 JSON object。")
        provider_unknown = sorted(set(provider).difference({"id", "model"}))
        if provider_unknown:
            raise ReviewError("provider 含未知欄位：" + ", ".join(provider_unknown))
        provider_id = provider.get("id")
        model = provider.get("model")
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ReviewError("provider.id 不可為空。")
        if not isinstance(model, str) or not model.strip():
            raise ReviewError("provider.model 不可為空。")

        request_payload = payload.get("request")
        if not isinstance(request_payload, Mapping):
            raise ReviewError("request 必須是 ULCS-Intent-Request object。")
        request_forbidden = sorted(_FORBIDDEN_PROPOSAL_FIELDS.intersection(request_payload))
        if request_forbidden:
            raise ReviewError(
                "Provider request 不得包含治理或執行欄位："
                + ", ".join(request_forbidden)
            )
        try:
            request = IntentRequest.from_mapping(
                request_payload,
                source=f"provider:{provider_id.strip()}",
            )
        except IntentCompileError as exc:
            raise ReviewError(str(exc)) from exc

        raw_notes = payload.get("notes", [])
        if not isinstance(raw_notes, Sequence) or isinstance(raw_notes, (str, bytes)):
            raise ReviewError("notes 必須是字串陣列。")
        notes = tuple(str(item).strip() for item in raw_notes)
        if any(not item for item in notes):
            raise ReviewError("notes 不可包含空字串。")

        confidence_value = payload.get("confidence")
        confidence: float | None
        if confidence_value is None:
            confidence = None
        elif isinstance(confidence_value, bool) or not isinstance(confidence_value, (int, float)):
            raise ReviewError("confidence 必須是 0 到 1 的數字。")
        else:
            confidence = float(confidence_value)
            if not 0.0 <= confidence <= 1.0:
                raise ReviewError("confidence 必須介於 0 與 1。")

        return cls(
            provider_id=provider_id.strip(),
            model=model.strip(),
            request=request,
            notes=notes,
            confidence=confidence,
            source=source,
        )

    @classmethod
    def read(cls, path: str | Path) -> "ProviderProposal":
        proposal_path = Path(path)
        try:
            payload = json.loads(proposal_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ReviewError(f"Provider Proposal 不是合法 JSON：{exc}") from exc
        return cls.from_mapping(payload, source=str(proposal_path.resolve()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": PROVIDER_PROPOSAL_FORMAT,
            "version": REVIEW_VERSION,
            "provider": {"id": self.provider_id, "model": self.model},
            "request": self.request.to_dict(),
            "notes": list(self.notes),
            "confidence": self.confidence,
        }

    @property
    def digest(self) -> str:
        return digest_mapping(self.to_dict())


@dataclass(frozen=True, slots=True)
class ReviewBundle:
    root: Path
    status: str
    proposal_digest: str
    files: Mapping[str, Mapping[str, Any]]
    digest: str

    @classmethod
    def build(
        cls,
        directory: str | Path,
        *,
        proposal_digest: str,
        status: str,
    ) -> "ReviewBundle":
        root = Path(directory).resolve()
        if status != "ready":
            raise ReviewError("只有 ready Intent Bundle 可以建立 Review Bundle。")
        files: dict[str, dict[str, Any]] = {}
        for name in _GENERATED_FILES:
            path = root / name
            if path.is_symlink():
                raise ReviewError(f"Review Bundle 不接受符號連結：{name}")
            if not path.is_file():
                raise ReviewError(f"Review Bundle 缺少必要檔案：{name}")
            files[name] = {
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
        unsigned = {
            "format": REVIEW_BUNDLE_FORMAT,
            "version": REVIEW_VERSION,
            "status": status,
            "proposal_digest": proposal_digest,
            "files": files,
        }
        digest = digest_mapping(unsigned)
        bundle = cls(
            root=root,
            status=status,
            proposal_digest=proposal_digest,
            files=files,
            digest=digest,
        )
        _atomic_write_json(root / "review-bundle.json", bundle.to_dict())
        return bundle

    @classmethod
    def read(cls, path: str | Path) -> "ReviewBundle":
        candidate = Path(path)
        manifest_path = candidate / "review-bundle.json" if candidate.is_dir() else candidate
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ReviewError(f"Review Bundle 不是合法 JSON：{exc}") from exc
        if not isinstance(payload, Mapping):
            raise ReviewError("Review Bundle 根節點必須是 JSON object。")
        if payload.get("format") != REVIEW_BUNDLE_FORMAT:
            raise ReviewError("Review Bundle format 不相容。")
        if str(payload.get("version")) != REVIEW_VERSION:
            raise ReviewError("Review Bundle version 不相容。")
        status = payload.get("status")
        proposal_digest = payload.get("proposal_digest")
        files = payload.get("files")
        digest = payload.get("digest")
        if status != "ready":
            raise ReviewError("Review Bundle status 必須是 ready。")
        if not _is_sha256(proposal_digest):
            raise ReviewError("proposal_digest 必須是 SHA-256。")
        if not isinstance(files, Mapping) or set(files) != set(_GENERATED_FILES):
            raise ReviewError("Review Bundle files 與 v0.8 必要檔案不一致。")
        normalized: dict[str, dict[str, Any]] = {}
        for name, metadata in files.items():
            if Path(name).name != name or "/" in name or "\\" in name:
                raise ReviewError(f"Review Bundle 檔名不安全：{name!r}")
            if not isinstance(metadata, Mapping):
                raise ReviewError(f"Review Bundle 檔案 metadata 無效：{name}")
            file_digest = metadata.get("sha256")
            size = metadata.get("size")
            if not _is_sha256(file_digest):
                raise ReviewError(f"Review Bundle 檔案摘要無效：{name}")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise ReviewError(f"Review Bundle 檔案大小無效：{name}")
            normalized[name] = {"sha256": file_digest, "size": size}
        unsigned = {
            "format": REVIEW_BUNDLE_FORMAT,
            "version": REVIEW_VERSION,
            "status": status,
            "proposal_digest": proposal_digest,
            "files": normalized,
        }
        expected = digest_mapping(unsigned)
        if not _is_sha256(digest) or not hmac.compare_digest(digest, expected):
            raise ReviewError("Review Bundle canonical digest 不一致。")
        bundle = cls(
            root=manifest_path.resolve().parent,
            status=status,
            proposal_digest=proposal_digest,
            files=normalized,
            digest=digest,
        )
        bundle.verify_files()
        return bundle

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": REVIEW_BUNDLE_FORMAT,
            "version": REVIEW_VERSION,
            "status": self.status,
            "proposal_digest": self.proposal_digest,
            "files": {name: dict(value) for name, value in self.files.items()},
            "digest": self.digest,
        }

    def verify_files(self) -> None:
        for name, metadata in self.files.items():
            path = self.root / name
            if path.is_symlink():
                raise ReviewError(f"Review Bundle 不接受符號連結：{name}")
            if not path.is_file():
                raise ReviewError(f"Review Bundle 檔案遺失：{name}")
            actual_size = path.stat().st_size
            if actual_size != metadata["size"]:
                raise ReviewError(f"Review Bundle 檔案大小已變更：{name}")
            actual_digest = sha256_file(path)
            if not hmac.compare_digest(actual_digest, metadata["sha256"]):
                raise ReviewError(f"Review Bundle 檔案摘要已變更：{name}")
        proposal = ProviderProposal.read(self.root / "provider-proposal.json")
        if not hmac.compare_digest(proposal.digest, self.proposal_digest):
            raise ReviewError("Provider Proposal canonical digest 已變更。")
        try:
            intent_metadata = json.loads(
                (self.root / "intent-bundle.json").read_text(encoding="utf-8")
            )
        except json.JSONDecodeError as exc:
            raise ReviewError(f"intent-bundle.json 不是合法 JSON：{exc}") from exc
        if not isinstance(intent_metadata, Mapping) or intent_metadata.get("status") != "ready":
            raise ReviewError("intent-bundle.json 未標記為 ready。")


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    bundle_digest: str
    decision: str
    approver: str
    scopes: tuple[str, ...]
    reason: str
    issued_at: str
    signature: str

    @classmethod
    def create(
        cls,
        bundle: ReviewBundle,
        *,
        decision: str,
        approver: str,
        key: bytes,
        scopes: Sequence[str] = ("execute",),
        reason: str = "",
        issued_at: str | None = None,
    ) -> "ApprovalRecord":
        bundle.verify_files()
        normalized_decision = decision.strip().lower()
        if normalized_decision not in {"approve", "reject"}:
            raise ReviewError("decision 必須是 approve 或 reject。")
        normalized_approver = approver.strip()
        if not normalized_approver:
            raise ReviewError("approver 不可為空。")
        normalized_scopes = tuple(dict.fromkeys(item.strip() for item in scopes if item.strip()))
        if not normalized_scopes:
            raise ReviewError("Approval Record 至少需要一個 scope。")
        _validate_key(key)
        timestamp = issued_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        unsigned = _approval_unsigned(
            bundle_digest=bundle.digest,
            decision=normalized_decision,
            approver=normalized_approver,
            scopes=normalized_scopes,
            reason=reason,
            issued_at=timestamp,
        )
        signature = hmac.new(key, canonical_bytes(unsigned), hashlib.sha256).hexdigest()
        return cls(
            bundle_digest=bundle.digest,
            decision=normalized_decision,
            approver=normalized_approver,
            scopes=normalized_scopes,
            reason=reason,
            issued_at=timestamp,
            signature=signature,
        )

    @classmethod
    def read(cls, path: str | Path) -> "ApprovalRecord":
        approval_path = Path(path)
        try:
            payload = json.loads(approval_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ReviewError(f"Approval Record 不是合法 JSON：{exc}") from exc
        if not isinstance(payload, Mapping):
            raise ReviewError("Approval Record 根節點必須是 JSON object。")
        if payload.get("format") != APPROVAL_FORMAT:
            raise ReviewError("Approval Record format 不相容。")
        if str(payload.get("version")) != REVIEW_VERSION:
            raise ReviewError("Approval Record version 不相容。")
        if payload.get("algorithm") != APPROVAL_ALGORITHM:
            raise ReviewError("Approval Record algorithm 不相容。")
        bundle_digest = payload.get("bundle_digest")
        decision = payload.get("decision")
        approver = payload.get("approver")
        scopes = payload.get("scopes")
        reason = payload.get("reason", "")
        issued_at = payload.get("issued_at")
        signature = payload.get("signature")
        if not _is_sha256(bundle_digest):
            raise ReviewError("Approval Record bundle_digest 無效。")
        if decision not in {"approve", "reject"}:
            raise ReviewError("Approval Record decision 無效。")
        if not isinstance(approver, str) or not approver.strip():
            raise ReviewError("Approval Record approver 無效。")
        if not isinstance(scopes, list) or not scopes or any(
            not isinstance(item, str) or not item.strip() for item in scopes
        ):
            raise ReviewError("Approval Record scopes 無效。")
        if not isinstance(reason, str) or not isinstance(issued_at, str):
            raise ReviewError("Approval Record reason 或 issued_at 無效。")
        if not _is_sha256(signature):
            raise ReviewError("Approval Record signature 無效。")
        return cls(
            bundle_digest=bundle_digest,
            decision=decision,
            approver=approver.strip(),
            scopes=tuple(scopes),
            reason=reason,
            issued_at=issued_at,
            signature=signature,
        )

    def unsigned_dict(self) -> dict[str, Any]:
        return _approval_unsigned(
            bundle_digest=self.bundle_digest,
            decision=self.decision,
            approver=self.approver,
            scopes=self.scopes,
            reason=self.reason,
            issued_at=self.issued_at,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = self.unsigned_dict()
        payload["signature"] = self.signature
        return payload

    def write(self, path: str | Path) -> None:
        _atomic_write_json(Path(path), self.to_dict())

    def verify_signature(self, key: bytes) -> None:
        _validate_key(key)
        expected = hmac.new(
            key,
            canonical_bytes(self.unsigned_dict()),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(self.signature, expected):
            raise ReviewError("Approval Record HMAC 驗證失敗。")


def compile_provider_proposal(
    proposal: ProviderProposal,
    directory: str | Path,
) -> tuple[IntentBundle, ReviewBundle | None]:
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    for name in (*_GENERATED_FILES, "review-bundle.json"):
        stale = target / name
        if stale.exists():
            if stale.is_dir():
                raise ReviewError(f"輸出路徑被目錄占用：{name}")
            stale.unlink()
    _atomic_write_json(target / "provider-proposal.json", proposal.to_dict())
    intent_bundle = compile_intent(proposal.request)
    intent_bundle.write(target)
    if not intent_bundle.ready:
        stale = target / "review-bundle.json"
        if stale.exists():
            stale.unlink()
        return intent_bundle, None
    review = ReviewBundle.build(
        target,
        proposal_digest=proposal.digest,
        status=intent_bundle.status,
    )
    return intent_bundle, review


def verify_approval(
    bundle: ReviewBundle,
    approval: ApprovalRecord,
    *,
    key: bytes,
    required_scope: str = "execute",
) -> None:
    bundle.verify_files()
    if not hmac.compare_digest(bundle.digest, approval.bundle_digest):
        raise ReviewError("Approval Record 綁定的是另一個 Review Bundle。")
    approval.verify_signature(key)
    if approval.decision != "approve":
        raise ReviewError("Approval Record 並未核准執行。")
    if required_scope not in approval.scopes:
        raise ReviewError(f"Approval Record 缺少必要 scope：{required_scope}")


def load_key(*, env_name: str | None = None, key_file: str | Path | None = None) -> bytes:
    if bool(env_name) == bool(key_file):
        raise ReviewError("必須且只能從環境變數或 key file 讀取 HMAC key。")
    if env_name:
        value = os.environ.get(env_name)
        if value is None:
            raise ReviewError(f"環境變數 {env_name} 未設定。")
        key = value.encode("utf-8")
    else:
        key = Path(key_file).read_bytes().strip()
    _validate_key(key)
    return key


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ReviewError(f"無法建立 canonical JSON：{exc}") from exc
    return encoded.encode("utf-8")


def digest_mapping(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _approval_unsigned(
    *,
    bundle_digest: str,
    decision: str,
    approver: str,
    scopes: Sequence[str],
    reason: str,
    issued_at: str,
) -> dict[str, Any]:
    return {
        "format": APPROVAL_FORMAT,
        "version": REVIEW_VERSION,
        "algorithm": APPROVAL_ALGORITHM,
        "bundle_digest": bundle_digest,
        "decision": decision,
        "approver": approver,
        "scopes": list(scopes),
        "reason": reason,
        "issued_at": issued_at,
    }


def _validate_key(key: bytes) -> None:
    if not isinstance(key, bytes) or len(key) < 16:
        raise ReviewError("HMAC key 至少需要 16 bytes。")


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(ch in "0123456789abcdef" for ch in value)


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
