from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .inputs_v09 import InputBundle
from .review import ProviderProposal, ReviewBundle, ReviewError, digest_mapping
from .v09_crypto import ALGORITHM, public_key_id, sign_mapping, verify_mapping

GOVERNANCE_VERSION = "0.9"
PROVIDER_ATTESTATION_FORMAT = "ULCS-Provider-Attestation"
SIGNED_APPROVAL_FORMAT = "ULCS-Signed-Approval"


@dataclass(frozen=True, slots=True)
class ProviderAttestation:
    proposal_digest: str
    provider_id: str
    model: str
    key_id: str
    issued_at: str
    signature: str

    @classmethod
    def create(
        cls,
        proposal: ProviderProposal,
        private_key: Ed25519PrivateKey,
        *,
        issued_at: str | None = None,
    ) -> "ProviderAttestation":
        timestamp = issued_at or _now()
        _validate_timestamp(timestamp, "issued_at")
        unsigned = _provider_unsigned(
            proposal_digest=proposal.digest,
            provider_id=proposal.provider_id,
            model=proposal.model,
            key_id=public_key_id(private_key.public_key()),
            issued_at=timestamp,
        )
        return cls(
            proposal_digest=proposal.digest,
            provider_id=proposal.provider_id,
            model=proposal.model,
            key_id=unsigned["provider"]["key_id"],
            issued_at=timestamp,
            signature=sign_mapping(private_key, unsigned),
        )

    @classmethod
    def read(cls, path: str | Path) -> "ProviderAttestation":
        payload = _read_json(path, "Provider Attestation")
        if set(payload) != {
            "format", "version", "algorithm", "proposal_digest", "provider", "issued_at", "signature"
        }:
            raise ReviewError("Provider Attestation 欄位集合不符合 v0.9。")
        if payload.get("format") != PROVIDER_ATTESTATION_FORMAT:
            raise ReviewError("Provider Attestation format 不相容。")
        if str(payload.get("version")) != GOVERNANCE_VERSION or payload.get("algorithm") != ALGORITHM:
            raise ReviewError("Provider Attestation version 或 algorithm 不相容。")
        proposal_digest = payload.get("proposal_digest")
        provider = payload.get("provider")
        issued_at = payload.get("issued_at")
        signature = payload.get("signature")
        if not _is_sha256(proposal_digest):
            raise ReviewError("Provider Attestation proposal_digest 無效。")
        if not isinstance(provider, Mapping) or set(provider) != {"id", "model", "key_id"}:
            raise ReviewError("Provider Attestation provider metadata 無效。")
        provider_id = provider.get("id")
        model = provider.get("model")
        key_id = provider.get("key_id")
        if not all(isinstance(item, str) and item.strip() for item in (provider_id, model, key_id)):
            raise ReviewError("Provider Attestation provider 欄位不可為空。")
        _validate_key_id(key_id)
        if not isinstance(issued_at, str) or not isinstance(signature, str):
            raise ReviewError("Provider Attestation issued_at 或 signature 無效。")
        _validate_timestamp(issued_at, "issued_at")
        return cls(
            proposal_digest=proposal_digest,
            provider_id=provider_id.strip(),
            model=model.strip(),
            key_id=key_id,
            issued_at=issued_at,
            signature=signature,
        )

    def unsigned_dict(self) -> dict[str, Any]:
        return _provider_unsigned(
            proposal_digest=self.proposal_digest,
            provider_id=self.provider_id,
            model=self.model,
            key_id=self.key_id,
            issued_at=self.issued_at,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = self.unsigned_dict()
        payload["signature"] = self.signature
        return payload

    @property
    def digest(self) -> str:
        return digest_mapping(self.to_dict())

    def verify(self, proposal: ProviderProposal, public_key: Ed25519PublicKey) -> None:
        if not hmac.compare_digest(self.proposal_digest, proposal.digest):
            raise ReviewError("Provider Attestation 綁定的是另一份 Provider Proposal。")
        if self.provider_id != proposal.provider_id or self.model != proposal.model:
            raise ReviewError("Provider Attestation 的 provider identity 與 Proposal 不一致。")
        actual_key_id = public_key_id(public_key)
        if not hmac.compare_digest(self.key_id, actual_key_id):
            raise ReviewError("Provider Attestation key_id 與 public key 不一致。")
        verify_mapping(public_key, self.unsigned_dict(), self.signature)

    def write(self, path: str | Path) -> None:
        _write_json(path, self.to_dict())


@dataclass(frozen=True, slots=True)
class SignedApproval:
    review_bundle_digest: str
    input_bundle_digest: str
    provider_attestation_digest: str
    decision: str
    approver_id: str
    key_id: str
    scopes: tuple[str, ...]
    reason: str
    issued_at: str
    signature: str

    @classmethod
    def create(
        cls,
        review: ReviewBundle,
        inputs: InputBundle,
        provider_attestation: ProviderAttestation,
        private_key: Ed25519PrivateKey,
        *,
        decision: str,
        approver: str,
        scopes: Sequence[str] = ("execute",),
        reason: str = "",
        issued_at: str | None = None,
    ) -> "SignedApproval":
        review.verify_files()
        inputs.verify()
        normalized_decision = decision.strip().lower()
        if normalized_decision not in {"approve", "reject"}:
            raise ReviewError("Signed Approval decision 必須是 approve 或 reject。")
        normalized_approver = approver.strip()
        if not normalized_approver:
            raise ReviewError("Signed Approval approver 不可為空。")
        normalized_scopes = tuple(dict.fromkeys(item.strip() for item in scopes if item.strip()))
        if not normalized_scopes:
            raise ReviewError("Signed Approval 至少需要一個 scope。")
        timestamp = issued_at or _now()
        _validate_timestamp(timestamp, "issued_at")
        key_id = public_key_id(private_key.public_key())
        unsigned = _approval_unsigned(
            review_bundle_digest=review.digest,
            input_bundle_digest=inputs.digest,
            provider_attestation_digest=provider_attestation.digest,
            decision=normalized_decision,
            approver_id=normalized_approver,
            key_id=key_id,
            scopes=normalized_scopes,
            reason=reason,
            issued_at=timestamp,
        )
        return cls(
            review_bundle_digest=review.digest,
            input_bundle_digest=inputs.digest,
            provider_attestation_digest=provider_attestation.digest,
            decision=normalized_decision,
            approver_id=normalized_approver,
            key_id=key_id,
            scopes=normalized_scopes,
            reason=reason,
            issued_at=timestamp,
            signature=sign_mapping(private_key, unsigned),
        )

    @classmethod
    def read(cls, path: str | Path) -> "SignedApproval":
        payload = _read_json(path, "Signed Approval")
        if set(payload) != {
            "format", "version", "algorithm", "review_bundle_digest", "input_bundle_digest",
            "provider_attestation_digest", "decision", "approver", "scopes", "reason",
            "issued_at", "signature"
        }:
            raise ReviewError("Signed Approval 欄位集合不符合 v0.9。")
        if payload.get("format") != SIGNED_APPROVAL_FORMAT:
            raise ReviewError("Signed Approval format 不相容。")
        if str(payload.get("version")) != GOVERNANCE_VERSION or payload.get("algorithm") != ALGORITHM:
            raise ReviewError("Signed Approval version 或 algorithm 不相容。")
        digests = (
            payload.get("review_bundle_digest"),
            payload.get("input_bundle_digest"),
            payload.get("provider_attestation_digest"),
        )
        if not all(_is_sha256(value) for value in digests):
            raise ReviewError("Signed Approval digest 欄位無效。")
        decision = payload.get("decision")
        if decision not in {"approve", "reject"}:
            raise ReviewError("Signed Approval decision 無效。")
        approver = payload.get("approver")
        if not isinstance(approver, Mapping) or set(approver) != {"id", "key_id"}:
            raise ReviewError("Signed Approval approver metadata 無效。")
        approver_id = approver.get("id")
        key_id = approver.get("key_id")
        if not isinstance(approver_id, str) or not approver_id.strip() or not isinstance(key_id, str):
            raise ReviewError("Signed Approval approver 欄位無效。")
        _validate_key_id(key_id)
        scopes = payload.get("scopes")
        if not isinstance(scopes, list) or not scopes or any(
            not isinstance(item, str) or not item.strip() for item in scopes
        ):
            raise ReviewError("Signed Approval scopes 無效。")
        if len(set(scopes)) != len(scopes):
            raise ReviewError("Signed Approval scopes 不可重複。")
        reason = payload.get("reason")
        issued_at = payload.get("issued_at")
        signature = payload.get("signature")
        if not isinstance(reason, str) or not isinstance(issued_at, str) or not isinstance(signature, str):
            raise ReviewError("Signed Approval reason、issued_at 或 signature 無效。")
        _validate_timestamp(issued_at, "issued_at")
        return cls(
            review_bundle_digest=digests[0],
            input_bundle_digest=digests[1],
            provider_attestation_digest=digests[2],
            decision=decision,
            approver_id=approver_id.strip(),
            key_id=key_id,
            scopes=tuple(scopes),
            reason=reason,
            issued_at=issued_at,
            signature=signature,
        )

    def unsigned_dict(self) -> dict[str, Any]:
        return _approval_unsigned(
            review_bundle_digest=self.review_bundle_digest,
            input_bundle_digest=self.input_bundle_digest,
            provider_attestation_digest=self.provider_attestation_digest,
            decision=self.decision,
            approver_id=self.approver_id,
            key_id=self.key_id,
            scopes=self.scopes,
            reason=self.reason,
            issued_at=self.issued_at,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = self.unsigned_dict()
        payload["signature"] = self.signature
        return payload

    @property
    def digest(self) -> str:
        return digest_mapping(self.to_dict())

    def verify(
        self,
        review: ReviewBundle,
        inputs: InputBundle,
        provider_attestation: ProviderAttestation,
        public_key: Ed25519PublicKey,
        *,
        required_scope: str = "execute",
    ) -> None:
        review.verify_files()
        inputs.verify()
        bindings = (
            (self.review_bundle_digest, review.digest, "Review Bundle"),
            (self.input_bundle_digest, inputs.digest, "Input Bundle"),
            (self.provider_attestation_digest, provider_attestation.digest, "Provider Attestation"),
        )
        for recorded, actual, label in bindings:
            if not hmac.compare_digest(recorded, actual):
                raise ReviewError(f"Signed Approval 綁定的是另一個 {label}。")
        actual_key_id = public_key_id(public_key)
        if not hmac.compare_digest(self.key_id, actual_key_id):
            raise ReviewError("Signed Approval key_id 與 public key 不一致。")
        verify_mapping(public_key, self.unsigned_dict(), self.signature)
        if self.decision != "approve":
            raise ReviewError("Signed Approval 並未核准執行。")
        if required_scope not in self.scopes:
            raise ReviewError(f"Signed Approval 缺少必要 scope：{required_scope}")

    def write(self, path: str | Path) -> None:
        _write_json(path, self.to_dict())


def _provider_unsigned(
    *,
    proposal_digest: str,
    provider_id: str,
    model: str,
    key_id: str,
    issued_at: str,
) -> dict[str, Any]:
    return {
        "format": PROVIDER_ATTESTATION_FORMAT,
        "version": GOVERNANCE_VERSION,
        "algorithm": ALGORITHM,
        "proposal_digest": proposal_digest,
        "provider": {"id": provider_id, "model": model, "key_id": key_id},
        "issued_at": issued_at,
    }


def _approval_unsigned(
    *,
    review_bundle_digest: str,
    input_bundle_digest: str,
    provider_attestation_digest: str,
    decision: str,
    approver_id: str,
    key_id: str,
    scopes: Sequence[str],
    reason: str,
    issued_at: str,
) -> dict[str, Any]:
    return {
        "format": SIGNED_APPROVAL_FORMAT,
        "version": GOVERNANCE_VERSION,
        "algorithm": ALGORITHM,
        "review_bundle_digest": review_bundle_digest,
        "input_bundle_digest": input_bundle_digest,
        "provider_attestation_digest": provider_attestation_digest,
        "decision": decision,
        "approver": {"id": approver_id, "key_id": key_id},
        "scopes": list(scopes),
        "reason": reason,
        "issued_at": issued_at,
    }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_timestamp(value: str, field: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewError(f"{field} 不是合法 ISO-8601 timestamp。") from exc
    if parsed.tzinfo is None:
        raise ReviewError(f"{field} 必須包含 timezone。")


def _validate_key_id(value: str) -> None:
    if not value.startswith("sha256:") or not _is_sha256(value[7:]):
        raise ReviewError("Ed25519 key_id 必須是 sha256:<64 hex>。")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        ch in "0123456789abcdef" for ch in value
    )


def _read_json(path: str | Path, label: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReviewError(f"{label} 不是合法 JSON：{exc}") from exc
    if not isinstance(payload, Mapping):
        raise ReviewError(f"{label} 根節點必須是 JSON object。")
    return payload


def _write_json(path: str | Path, payload: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    target.write_text(content, encoding="utf-8", newline="\n")
