from __future__ import annotations

import hmac
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .attestations_v09 import ProviderAttestation, SignedApproval
from .inputs_v09 import InputBundle
from .review import ReviewBundle, ReviewError, digest_mapping
from .transparency_v09 import TransparencyCheckpoint, TransparencyLog
from .v09_crypto import ALGORITHM, public_key_id, sign_mapping, verify_mapping

GOVERNANCE_V10_VERSION = "1.0"
TRUST_REGISTRY_FORMAT = "ULCS-Trust-Registry"
APPROVAL_SET_FORMAT = "ULCS-Threshold-Approval-Set"
WITNESS_STATEMENT_FORMAT = "ULCS-Checkpoint-Witness"
WITNESS_SET_FORMAT = "ULCS-Witness-Set"

_ALLOWED_ROLES = {"provider", "approver", "checkpoint", "witness", "release"}


@dataclass(frozen=True, slots=True)
class RegistryKey:
    principal: str
    key_id: str
    roles: tuple[str, ...]
    status: str
    public_key_pem: str

    @classmethod
    def from_public_key(
        cls,
        *,
        principal: str,
        roles: Sequence[str],
        public_key: Ed25519PublicKey,
        status: str = "active",
    ) -> "RegistryKey":
        normalized_principal = principal.strip()
        if not normalized_principal:
            raise ReviewError("Trust Registry principal 不可為空。")
        normalized_roles = _normalize_roles(roles)
        normalized_status = status.strip().lower()
        if normalized_status not in {"active", "revoked"}:
            raise ReviewError("Trust Registry key status 必須是 active 或 revoked。")
        pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")
        return cls(
            principal=normalized_principal,
            key_id=public_key_id(public_key),
            roles=normalized_roles,
            status=normalized_status,
            public_key_pem=pem,
        )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RegistryKey":
        required = {"principal", "key_id", "roles", "status", "public_key"}
        if not isinstance(payload, Mapping) or set(payload) != required:
            raise ReviewError("Trust Registry key 欄位集合無效。")
        principal = payload.get("principal")
        key_id = payload.get("key_id")
        roles = payload.get("roles")
        status = payload.get("status")
        pem = payload.get("public_key")
        if not isinstance(principal, str) or not principal.strip():
            raise ReviewError("Trust Registry key principal 無效。")
        if not isinstance(key_id, str):
            raise ReviewError("Trust Registry key_id 無效。")
        _validate_key_id(key_id)
        if not isinstance(roles, list):
            raise ReviewError("Trust Registry roles 必須是陣列。")
        normalized_roles = _normalize_roles(roles)
        if status not in {"active", "revoked"}:
            raise ReviewError("Trust Registry key status 無效。")
        if not isinstance(pem, str) or not pem.strip():
            raise ReviewError("Trust Registry public_key 不可為空。")
        key = _load_public_key_pem(pem)
        if not hmac.compare_digest(public_key_id(key), key_id):
            raise ReviewError("Trust Registry key_id 與 public_key 不一致。")
        return cls(
            principal=principal.strip(),
            key_id=key_id,
            roles=normalized_roles,
            status=status,
            public_key_pem=pem,
        )

    def public_key(self) -> Ed25519PublicKey:
        return _load_public_key_pem(self.public_key_pem)

    def to_dict(self) -> dict[str, Any]:
        return {
            "principal": self.principal,
            "key_id": self.key_id,
            "roles": list(self.roles),
            "status": self.status,
            "public_key": self.public_key_pem,
        }


@dataclass(frozen=True, slots=True)
class TrustPolicy:
    approval_threshold: int
    witness_threshold: int
    required_approval_scopes: tuple[str, ...]
    distinct_approver_principals: bool = True

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "TrustPolicy":
        required = {
            "approval_threshold",
            "witness_threshold",
            "required_approval_scopes",
            "distinct_approver_principals",
        }
        if not isinstance(payload, Mapping) or set(payload) != required:
            raise ReviewError("Trust Registry policy 欄位集合無效。")
        approval_threshold = payload.get("approval_threshold")
        witness_threshold = payload.get("witness_threshold")
        scopes = payload.get("required_approval_scopes")
        distinct = payload.get("distinct_approver_principals")
        if isinstance(approval_threshold, bool) or not isinstance(approval_threshold, int) or approval_threshold < 1:
            raise ReviewError("approval_threshold 必須是至少 1 的整數。")
        if isinstance(witness_threshold, bool) or not isinstance(witness_threshold, int) or witness_threshold < 1:
            raise ReviewError("witness_threshold 必須是至少 1 的整數。")
        if not isinstance(scopes, list) or not scopes:
            raise ReviewError("required_approval_scopes 必須是非空陣列。")
        normalized_scopes = tuple(dict.fromkeys(_require_nonempty_string(item, "approval scope") for item in scopes))
        if len(normalized_scopes) != len(scopes):
            raise ReviewError("required_approval_scopes 不可重複。")
        if not isinstance(distinct, bool):
            raise ReviewError("distinct_approver_principals 必須是布林值。")
        return cls(
            approval_threshold=approval_threshold,
            witness_threshold=witness_threshold,
            required_approval_scopes=normalized_scopes,
            distinct_approver_principals=distinct,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_threshold": self.approval_threshold,
            "witness_threshold": self.witness_threshold,
            "required_approval_scopes": list(self.required_approval_scopes),
            "distinct_approver_principals": self.distinct_approver_principals,
        }


@dataclass(frozen=True, slots=True)
class TrustRegistry:
    registry_id: str
    issued_at: str
    root_key_id: str
    policy: TrustPolicy
    keys: tuple[RegistryKey, ...]
    signature: str

    @classmethod
    def create(
        cls,
        *,
        registry_id: str,
        policy: TrustPolicy,
        keys: Sequence[RegistryKey],
        root_private_key: Ed25519PrivateKey,
        issued_at: str | None = None,
    ) -> "TrustRegistry":
        normalized_id = registry_id.strip()
        if not normalized_id:
            raise ReviewError("Trust Registry registry_id 不可為空。")
        timestamp = issued_at or _now()
        _validate_timestamp(timestamp)
        normalized_keys = tuple(keys)
        _validate_registry_keys(normalized_keys, policy)
        root_key_id = public_key_id(root_private_key.public_key())
        unsigned = _registry_unsigned(
            registry_id=normalized_id,
            issued_at=timestamp,
            root_key_id=root_key_id,
            policy=policy,
            keys=normalized_keys,
        )
        return cls(
            registry_id=normalized_id,
            issued_at=timestamp,
            root_key_id=root_key_id,
            policy=policy,
            keys=normalized_keys,
            signature=sign_mapping(root_private_key, unsigned),
        )

    @classmethod
    def read(cls, path: str | Path) -> "TrustRegistry":
        payload = _read_json(path, "Trust Registry")
        required = {"format", "version", "algorithm", "registry_id", "issued_at", "root", "policy", "keys", "signature"}
        if set(payload) != required:
            raise ReviewError("Trust Registry 欄位集合不符合 v1.0。")
        if payload.get("format") != TRUST_REGISTRY_FORMAT or str(payload.get("version")) != GOVERNANCE_V10_VERSION:
            raise ReviewError("Trust Registry format 或 version 不相容。")
        if payload.get("algorithm") != ALGORITHM:
            raise ReviewError("Trust Registry algorithm 不相容。")
        registry_id = payload.get("registry_id")
        issued_at = payload.get("issued_at")
        root = payload.get("root")
        policy_payload = payload.get("policy")
        raw_keys = payload.get("keys")
        signature = payload.get("signature")
        if not isinstance(registry_id, str) or not registry_id.strip():
            raise ReviewError("Trust Registry registry_id 無效。")
        if not isinstance(issued_at, str):
            raise ReviewError("Trust Registry issued_at 無效。")
        _validate_timestamp(issued_at)
        if not isinstance(root, Mapping) or set(root) != {"key_id"} or not isinstance(root.get("key_id"), str):
            raise ReviewError("Trust Registry root metadata 無效。")
        _validate_key_id(root["key_id"])
        if not isinstance(policy_payload, Mapping):
            raise ReviewError("Trust Registry policy 必須是 JSON object。")
        policy = TrustPolicy.from_mapping(policy_payload)
        if not isinstance(raw_keys, list) or not raw_keys:
            raise ReviewError("Trust Registry keys 必須是非空陣列。")
        keys = tuple(RegistryKey.from_mapping(item) for item in raw_keys)
        _validate_registry_keys(keys, policy)
        if not isinstance(signature, str):
            raise ReviewError("Trust Registry signature 無效。")
        return cls(
            registry_id=registry_id.strip(),
            issued_at=issued_at,
            root_key_id=root["key_id"],
            policy=policy,
            keys=keys,
            signature=signature,
        )

    def unsigned_dict(self) -> dict[str, Any]:
        return _registry_unsigned(
            registry_id=self.registry_id,
            issued_at=self.issued_at,
            root_key_id=self.root_key_id,
            policy=self.policy,
            keys=self.keys,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = self.unsigned_dict()
        payload["signature"] = self.signature
        return payload

    @property
    def digest(self) -> str:
        return digest_mapping(self.to_dict())

    def verify(self, root_public_key: Ed25519PublicKey) -> None:
        actual_root = public_key_id(root_public_key)
        if not hmac.compare_digest(self.root_key_id, actual_root):
            raise ReviewError("Trust Registry root key 與外部 trust anchor 不一致。")
        _validate_registry_keys(self.keys, self.policy)
        verify_mapping(root_public_key, self.unsigned_dict(), self.signature)

    def resolve(
        self,
        key_id: str,
        *,
        role: str,
        principal: str | None = None,
    ) -> RegistryKey:
        if role not in _ALLOWED_ROLES:
            raise ReviewError(f"未知 Trust Registry role：{role}")
        matches = [entry for entry in self.keys if hmac.compare_digest(entry.key_id, key_id)]
        if not matches:
            raise ReviewError(f"Trust Registry 未授權 key：{key_id}")
        entry = matches[0]
        if entry.status != "active":
            raise ReviewError(f"Trust Registry key 已撤銷：{key_id}")
        if role not in entry.roles:
            raise ReviewError(f"Trust Registry key 缺少 role={role}：{key_id}")
        if principal is not None and entry.principal != principal:
            raise ReviewError(
                f"Trust Registry principal 不一致：expected={principal}; actual={entry.principal}"
            )
        return entry

    def write(self, path: str | Path) -> None:
        _atomic_write_json(Path(path), self.to_dict())


@dataclass(frozen=True, slots=True)
class ApprovalSet:
    review_bundle_digest: str
    input_bundle_digest: str
    provider_attestation_digest: str
    threshold: int
    distinct_principals: bool
    approvals: tuple[SignedApproval, ...]
    created_at: str

    @classmethod
    def create(
        cls,
        review: ReviewBundle,
        inputs: InputBundle,
        provider_attestation: ProviderAttestation,
        approvals: Sequence[SignedApproval],
        registry: TrustRegistry,
        log: TransparencyLog,
        *,
        created_at: str | None = None,
    ) -> "ApprovalSet":
        timestamp = created_at or _now()
        _validate_timestamp(timestamp)
        result = cls(
            review_bundle_digest=review.digest,
            input_bundle_digest=inputs.digest,
            provider_attestation_digest=provider_attestation.digest,
            threshold=registry.policy.approval_threshold,
            distinct_principals=registry.policy.distinct_approver_principals,
            approvals=tuple(approvals),
            created_at=timestamp,
        )
        result.verify(review, inputs, provider_attestation, registry, log)
        return result

    @classmethod
    def read(cls, path: str | Path) -> "ApprovalSet":
        payload = _read_json(path, "Threshold Approval Set")
        required = {
            "format", "version", "review_bundle_digest", "input_bundle_digest",
            "provider_attestation_digest", "threshold", "distinct_principals",
            "approvals", "created_at", "digest"
        }
        if set(payload) != required:
            raise ReviewError("Threshold Approval Set 欄位集合不符合 v1.0。")
        if payload.get("format") != APPROVAL_SET_FORMAT or str(payload.get("version")) != GOVERNANCE_V10_VERSION:
            raise ReviewError("Threshold Approval Set format 或 version 不相容。")
        digests = (
            payload.get("review_bundle_digest"),
            payload.get("input_bundle_digest"),
            payload.get("provider_attestation_digest"),
        )
        if not all(_is_sha256(item) for item in digests):
            raise ReviewError("Threshold Approval Set binding digest 無效。")
        threshold = payload.get("threshold")
        distinct = payload.get("distinct_principals")
        raw_approvals = payload.get("approvals")
        created_at = payload.get("created_at")
        digest = payload.get("digest")
        if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 1:
            raise ReviewError("Threshold Approval Set threshold 無效。")
        if not isinstance(distinct, bool):
            raise ReviewError("Threshold Approval Set distinct_principals 無效。")
        if not isinstance(raw_approvals, list) or not raw_approvals:
            raise ReviewError("Threshold Approval Set approvals 必須是非空陣列。")
        approvals = tuple(_approval_from_mapping(item) for item in raw_approvals)
        if not isinstance(created_at, str):
            raise ReviewError("Threshold Approval Set created_at 無效。")
        _validate_timestamp(created_at)
        if not _is_sha256(digest):
            raise ReviewError("Threshold Approval Set digest 無效。")
        result = cls(
            review_bundle_digest=digests[0],
            input_bundle_digest=digests[1],
            provider_attestation_digest=digests[2],
            threshold=threshold,
            distinct_principals=distinct,
            approvals=approvals,
            created_at=created_at,
        )
        if not hmac.compare_digest(result.digest, digest):
            raise ReviewError("Threshold Approval Set canonical digest 不一致。")
        return result

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "format": APPROVAL_SET_FORMAT,
            "version": GOVERNANCE_V10_VERSION,
            "review_bundle_digest": self.review_bundle_digest,
            "input_bundle_digest": self.input_bundle_digest,
            "provider_attestation_digest": self.provider_attestation_digest,
            "threshold": self.threshold,
            "distinct_principals": self.distinct_principals,
            "approvals": [item.to_dict() for item in self.approvals],
            "created_at": self.created_at,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self.unsigned_dict()
        payload["digest"] = self.digest
        return payload

    @property
    def digest(self) -> str:
        return digest_mapping(self.unsigned_dict())

    def verify(
        self,
        review: ReviewBundle,
        inputs: InputBundle,
        provider_attestation: ProviderAttestation,
        registry: TrustRegistry,
        log: TransparencyLog,
    ) -> None:
        bindings = (
            (self.review_bundle_digest, review.digest, "Review Bundle"),
            (self.input_bundle_digest, inputs.digest, "Input Bundle"),
            (self.provider_attestation_digest, provider_attestation.digest, "Provider Attestation"),
        )
        for recorded, actual, label in bindings:
            if not hmac.compare_digest(recorded, actual):
                raise ReviewError(f"Threshold Approval Set 綁定的是另一個 {label}。")
        if self.threshold != registry.policy.approval_threshold:
            raise ReviewError("Threshold Approval Set threshold 與 Trust Registry 不一致。")
        if self.distinct_principals != registry.policy.distinct_approver_principals:
            raise ReviewError("Threshold Approval Set distinct policy 與 Trust Registry 不一致。")
        if len(self.approvals) < self.threshold:
            raise ReviewError(
                f"Threshold Approval Set 核准數不足：{len(self.approvals)} < {self.threshold}"
            )
        key_ids: set[str] = set()
        principals: set[str] = set()
        for approval in self.approvals:
            if approval.key_id in key_ids:
                raise ReviewError("Threshold Approval Set 不可重複使用同一把 Approver key。")
            entry = registry.resolve(
                approval.key_id,
                role="approver",
                principal=approval.approver_id,
            )
            for scope in registry.policy.required_approval_scopes:
                approval.verify(
                    review,
                    inputs,
                    provider_attestation,
                    entry.public_key(),
                    required_scope=scope,
                )
            if not log.contains(event="approval-issued", subject=approval.digest):
                raise ReviewError("Transparency Log 缺少 Threshold Approval Set 中的核准。")
            if log.key_revoked(approval.key_id):
                raise ReviewError(f"Approver key 已在 Transparency Log 中撤銷：{approval.key_id}")
            key_ids.add(approval.key_id)
            principals.add(entry.principal)
        if self.distinct_principals and len(principals) < self.threshold:
            raise ReviewError(
                f"Distinct Approver principal 數不足：{len(principals)} < {self.threshold}"
            )

    def write(self, path: str | Path) -> None:
        _atomic_write_json(Path(path), self.to_dict())


@dataclass(frozen=True, slots=True)
class WitnessStatement:
    checkpoint_digest: str
    log_head: str
    entry_count: int
    witness_id: str
    key_id: str
    issued_at: str
    signature: str

    @classmethod
    def create(
        cls,
        checkpoint: TransparencyCheckpoint,
        private_key: Ed25519PrivateKey,
        *,
        witness: str,
        issued_at: str | None = None,
    ) -> "WitnessStatement":
        witness_id = witness.strip()
        if not witness_id:
            raise ReviewError("Witness id 不可為空。")
        timestamp = issued_at or _now()
        _validate_timestamp(timestamp)
        key_id = public_key_id(private_key.public_key())
        unsigned = _witness_unsigned(
            checkpoint_digest=digest_mapping(checkpoint.to_dict()),
            log_head=checkpoint.log_head,
            entry_count=checkpoint.entry_count,
            witness_id=witness_id,
            key_id=key_id,
            issued_at=timestamp,
        )
        return cls(
            checkpoint_digest=unsigned["checkpoint_digest"],
            log_head=checkpoint.log_head,
            entry_count=checkpoint.entry_count,
            witness_id=witness_id,
            key_id=key_id,
            issued_at=timestamp,
            signature=sign_mapping(private_key, unsigned),
        )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "WitnessStatement":
        required = {
            "format", "version", "algorithm", "checkpoint_digest", "log_head",
            "entry_count", "witness", "issued_at", "signature"
        }
        if not isinstance(payload, Mapping) or set(payload) != required:
            raise ReviewError("Checkpoint Witness 欄位集合不符合 v1.0。")
        if payload.get("format") != WITNESS_STATEMENT_FORMAT or str(payload.get("version")) != GOVERNANCE_V10_VERSION:
            raise ReviewError("Checkpoint Witness format 或 version 不相容。")
        if payload.get("algorithm") != ALGORITHM:
            raise ReviewError("Checkpoint Witness algorithm 不相容。")
        checkpoint_digest = payload.get("checkpoint_digest")
        log_head = payload.get("log_head")
        entry_count = payload.get("entry_count")
        witness = payload.get("witness")
        issued_at = payload.get("issued_at")
        signature = payload.get("signature")
        if not _is_sha256(checkpoint_digest) or not _is_sha256(log_head):
            raise ReviewError("Checkpoint Witness digest 無效。")
        if isinstance(entry_count, bool) or not isinstance(entry_count, int) or entry_count <= 0:
            raise ReviewError("Checkpoint Witness entry_count 無效。")
        if not isinstance(witness, Mapping) or set(witness) != {"id", "key_id"}:
            raise ReviewError("Checkpoint Witness metadata 無效。")
        witness_id = witness.get("id")
        key_id = witness.get("key_id")
        if not isinstance(witness_id, str) or not witness_id.strip() or not isinstance(key_id, str):
            raise ReviewError("Checkpoint Witness identity 無效。")
        _validate_key_id(key_id)
        if not isinstance(issued_at, str) or not isinstance(signature, str):
            raise ReviewError("Checkpoint Witness issued_at 或 signature 無效。")
        _validate_timestamp(issued_at)
        return cls(
            checkpoint_digest=checkpoint_digest,
            log_head=log_head,
            entry_count=entry_count,
            witness_id=witness_id.strip(),
            key_id=key_id,
            issued_at=issued_at,
            signature=signature,
        )

    @classmethod
    def read(cls, path: str | Path) -> "WitnessStatement":
        return cls.from_mapping(_read_json(path, "Checkpoint Witness"))

    def unsigned_dict(self) -> dict[str, Any]:
        return _witness_unsigned(
            checkpoint_digest=self.checkpoint_digest,
            log_head=self.log_head,
            entry_count=self.entry_count,
            witness_id=self.witness_id,
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

    def verify(self, checkpoint: TransparencyCheckpoint, public_key: Ed25519PublicKey) -> None:
        actual_checkpoint_digest = digest_mapping(checkpoint.to_dict())
        if not hmac.compare_digest(self.checkpoint_digest, actual_checkpoint_digest):
            raise ReviewError("Checkpoint Witness 綁定的是另一個 checkpoint。")
        if not hmac.compare_digest(self.log_head, checkpoint.log_head):
            raise ReviewError("Checkpoint Witness log_head 不一致。")
        if self.entry_count != checkpoint.entry_count:
            raise ReviewError("Checkpoint Witness entry_count 不一致。")
        if not hmac.compare_digest(self.key_id, public_key_id(public_key)):
            raise ReviewError("Checkpoint Witness key_id 與 public key 不一致。")
        verify_mapping(public_key, self.unsigned_dict(), self.signature)

    def write(self, path: str | Path) -> None:
        _atomic_write_json(Path(path), self.to_dict())


@dataclass(frozen=True, slots=True)
class WitnessSet:
    checkpoint_digest: str
    threshold: int
    witnesses: tuple[WitnessStatement, ...]
    created_at: str

    @classmethod
    def create(
        cls,
        checkpoint: TransparencyCheckpoint,
        witnesses: Sequence[WitnessStatement],
        registry: TrustRegistry,
        *,
        log: TransparencyLog | None = None,
        created_at: str | None = None,
    ) -> "WitnessSet":
        timestamp = created_at or _now()
        _validate_timestamp(timestamp)
        result = cls(
            checkpoint_digest=digest_mapping(checkpoint.to_dict()),
            threshold=registry.policy.witness_threshold,
            witnesses=tuple(witnesses),
            created_at=timestamp,
        )
        result.verify(checkpoint, registry, log=log)
        return result

    @classmethod
    def read(cls, path: str | Path) -> "WitnessSet":
        payload = _read_json(path, "Witness Set")
        required = {"format", "version", "checkpoint_digest", "threshold", "witnesses", "created_at", "digest"}
        if set(payload) != required:
            raise ReviewError("Witness Set 欄位集合不符合 v1.0。")
        if payload.get("format") != WITNESS_SET_FORMAT or str(payload.get("version")) != GOVERNANCE_V10_VERSION:
            raise ReviewError("Witness Set format 或 version 不相容。")
        checkpoint_digest = payload.get("checkpoint_digest")
        threshold = payload.get("threshold")
        raw_witnesses = payload.get("witnesses")
        created_at = payload.get("created_at")
        digest = payload.get("digest")
        if not _is_sha256(checkpoint_digest):
            raise ReviewError("Witness Set checkpoint_digest 無效。")
        if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 1:
            raise ReviewError("Witness Set threshold 無效。")
        if not isinstance(raw_witnesses, list) or not raw_witnesses:
            raise ReviewError("Witness Set witnesses 必須是非空陣列。")
        witnesses = tuple(WitnessStatement.from_mapping(item) for item in raw_witnesses)
        if not isinstance(created_at, str):
            raise ReviewError("Witness Set created_at 無效。")
        _validate_timestamp(created_at)
        if not _is_sha256(digest):
            raise ReviewError("Witness Set digest 無效。")
        result = cls(
            checkpoint_digest=checkpoint_digest,
            threshold=threshold,
            witnesses=witnesses,
            created_at=created_at,
        )
        if not hmac.compare_digest(result.digest, digest):
            raise ReviewError("Witness Set canonical digest 不一致。")
        return result

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "format": WITNESS_SET_FORMAT,
            "version": GOVERNANCE_V10_VERSION,
            "checkpoint_digest": self.checkpoint_digest,
            "threshold": self.threshold,
            "witnesses": [item.to_dict() for item in self.witnesses],
            "created_at": self.created_at,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self.unsigned_dict()
        payload["digest"] = self.digest
        return payload

    @property
    def digest(self) -> str:
        return digest_mapping(self.unsigned_dict())

    def verify(
        self,
        checkpoint: TransparencyCheckpoint,
        registry: TrustRegistry,
        *,
        log: TransparencyLog | None = None,
    ) -> None:
        actual_checkpoint_digest = digest_mapping(checkpoint.to_dict())
        if not hmac.compare_digest(self.checkpoint_digest, actual_checkpoint_digest):
            raise ReviewError("Witness Set 綁定的是另一個 checkpoint。")
        if self.threshold != registry.policy.witness_threshold:
            raise ReviewError("Witness Set threshold 與 Trust Registry 不一致。")
        if len(self.witnesses) < self.threshold:
            raise ReviewError(
                f"Witness Set 見證數不足：{len(self.witnesses)} < {self.threshold}"
            )
        key_ids: set[str] = set()
        principals: set[str] = set()
        for witness in self.witnesses:
            if witness.key_id in key_ids:
                raise ReviewError("Witness Set 不可重複使用同一把 Witness key。")
            entry = registry.resolve(
                witness.key_id,
                role="witness",
                principal=witness.witness_id,
            )
            witness.verify(checkpoint, entry.public_key())
            if log is not None and log.key_revoked(witness.key_id):
                raise ReviewError(f"Witness key 已在 Transparency Log 中撤銷：{witness.key_id}")
            key_ids.add(witness.key_id)
            principals.add(entry.principal)
        if len(principals) < self.threshold:
            raise ReviewError(
                f"Distinct Witness principal 數不足：{len(principals)} < {self.threshold}"
            )

    def write(self, path: str | Path) -> None:
        _atomic_write_json(Path(path), self.to_dict())


def registry_key_from_file(
    *,
    principal: str,
    roles: Sequence[str],
    public_key_path: str | Path,
    status: str = "active",
) -> RegistryKey:
    try:
        raw = Path(public_key_path).read_bytes()
        key = serialization.load_pem_public_key(raw)
    except (OSError, TypeError, ValueError) as exc:
        raise ReviewError(f"無法讀取 Trust Registry public key：{exc}") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise ReviewError("Trust Registry 僅支援 Ed25519 public key。")
    return RegistryKey.from_public_key(
        principal=principal,
        roles=roles,
        public_key=key,
        status=status,
    )


def _registry_unsigned(
    *,
    registry_id: str,
    issued_at: str,
    root_key_id: str,
    policy: TrustPolicy,
    keys: Sequence[RegistryKey],
) -> dict[str, Any]:
    return {
        "format": TRUST_REGISTRY_FORMAT,
        "version": GOVERNANCE_V10_VERSION,
        "algorithm": ALGORITHM,
        "registry_id": registry_id,
        "issued_at": issued_at,
        "root": {"key_id": root_key_id},
        "policy": policy.to_dict(),
        "keys": [entry.to_dict() for entry in keys],
    }


def _witness_unsigned(
    *,
    checkpoint_digest: str,
    log_head: str,
    entry_count: int,
    witness_id: str,
    key_id: str,
    issued_at: str,
) -> dict[str, Any]:
    return {
        "format": WITNESS_STATEMENT_FORMAT,
        "version": GOVERNANCE_V10_VERSION,
        "algorithm": ALGORITHM,
        "checkpoint_digest": checkpoint_digest,
        "log_head": log_head,
        "entry_count": entry_count,
        "witness": {"id": witness_id, "key_id": key_id},
        "issued_at": issued_at,
    }


def _validate_registry_keys(keys: Sequence[RegistryKey], policy: TrustPolicy) -> None:
    if not keys:
        raise ReviewError("Trust Registry 至少需要一把 operational key。")
    key_ids = [entry.key_id for entry in keys]
    if len(set(key_ids)) != len(key_ids):
        raise ReviewError("Trust Registry key_id 不可重複。")
    active_by_role = {
        role: [entry for entry in keys if entry.status == "active" and role in entry.roles]
        for role in _ALLOWED_ROLES
    }
    for role in ("provider", "checkpoint", "release"):
        if not active_by_role[role]:
            raise ReviewError(f"Trust Registry 缺少 active role={role} key。")
    approver_principals = {entry.principal for entry in active_by_role["approver"]}
    if len(approver_principals) < policy.approval_threshold:
        raise ReviewError("Trust Registry active Approver principals 少於 approval_threshold。")
    witness_principals = {entry.principal for entry in active_by_role["witness"]}
    if len(witness_principals) < policy.witness_threshold:
        raise ReviewError("Trust Registry active Witness principals 少於 witness_threshold。")


def _normalize_roles(values: Sequence[Any]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(_require_nonempty_string(value, "role").lower() for value in values))
    if not normalized:
        raise ReviewError("Trust Registry key 至少需要一個 role。")
    unknown = set(normalized) - _ALLOWED_ROLES
    if unknown:
        raise ReviewError(f"Trust Registry 包含未知 roles：{sorted(unknown)}")
    return normalized


def _approval_from_mapping(payload: Mapping[str, Any]) -> SignedApproval:
    required = {
        "format", "version", "algorithm", "review_bundle_digest", "input_bundle_digest",
        "provider_attestation_digest", "decision", "approver", "scopes", "reason",
        "issued_at", "signature"
    }
    if not isinstance(payload, Mapping) or set(payload) != required:
        raise ReviewError("Threshold Approval Set 內含的 Signed Approval 欄位無效。")
    if payload.get("format") != "ULCS-Signed-Approval" or str(payload.get("version")) != "0.9":
        raise ReviewError("Threshold Approval Set 僅接受 v0.9 Signed Approval。")
    if payload.get("algorithm") != ALGORITHM:
        raise ReviewError("Signed Approval algorithm 不相容。")
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
        raise ReviewError("Signed Approval approver identity 無效。")
    _validate_key_id(key_id)
    scopes = payload.get("scopes")
    if not isinstance(scopes, list) or not scopes or any(not isinstance(item, str) or not item.strip() for item in scopes):
        raise ReviewError("Signed Approval scopes 無效。")
    if len(set(scopes)) != len(scopes):
        raise ReviewError("Signed Approval scopes 不可重複。")
    reason = payload.get("reason")
    issued_at = payload.get("issued_at")
    signature = payload.get("signature")
    if not isinstance(reason, str) or not isinstance(issued_at, str) or not isinstance(signature, str):
        raise ReviewError("Signed Approval metadata 無效。")
    _validate_timestamp(issued_at)
    return SignedApproval(
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


def _load_public_key_pem(value: str) -> Ed25519PublicKey:
    try:
        key = serialization.load_pem_public_key(value.encode("ascii"))
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ReviewError(f"Trust Registry public key PEM 無效：{exc}") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise ReviewError("Trust Registry public key 不是 Ed25519 key。")
    return key


def _require_nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewError(f"{label} 必須是非空字串。")
    return value.strip()


def _validate_key_id(value: str) -> None:
    if not value.startswith("sha256:") or not _is_sha256(value[7:]):
        raise ReviewError("key_id 必須是 sha256:<64 hex>。")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_timestamp(value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewError("Governance timestamp 不是合法 ISO-8601。") from exc
    if parsed.tzinfo is None:
        raise ReviewError("Governance timestamp 必須包含 timezone。")


def _read_json(path: str | Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReviewError(f"{label} 不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise ReviewError(f"{label} 根節點必須是 JSON object。")
    return payload


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
