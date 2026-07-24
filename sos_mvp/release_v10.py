from __future__ import annotations

import hmac
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .attestations_v09 import ProviderAttestation
from .governance_v10 import ApprovalSet, TrustRegistry, WitnessSet
from .inputs_v09 import InputBundle
from .review import ProviderProposal, ReviewBundle, ReviewError, digest_mapping, sha256_file
from .transparency_v09 import TransparencyCheckpoint, TransparencyLog
from .v09_crypto import ALGORITHM, public_key_id, sign_mapping, verify_mapping

RELEASE_VERSION = "1.0"
RELEASE_MANIFEST_FORMAT = "ULCS-Governed-Release-Bundle"


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    registry_digest: str
    review_bundle_digest: str
    input_bundle_digest: str
    provider_attestation_digest: str
    approval_set_digest: str
    transparency_log_digest: str
    checkpoint_digest: str
    witness_set_digest: str
    files: Mapping[str, Mapping[str, Any]]
    signer_id: str
    key_id: str
    created_at: str
    signature: str

    @classmethod
    def create(
        cls,
        *,
        registry: TrustRegistry,
        review: ReviewBundle,
        inputs: InputBundle,
        attestation: ProviderAttestation,
        approval_set: ApprovalSet,
        log: TransparencyLog,
        checkpoint: TransparencyCheckpoint,
        witness_set: WitnessSet,
        files: Mapping[str, Mapping[str, Any]],
        signer: str,
        private_key: Ed25519PrivateKey,
        created_at: str | None = None,
    ) -> "ReleaseManifest":
        signer_id = signer.strip()
        if not signer_id:
            raise ReviewError("Release signer 不可為空。")
        timestamp = created_at or _now()
        _validate_timestamp(timestamp)
        key_id = public_key_id(private_key.public_key())
        unsigned = _release_unsigned(
            registry_digest=registry.digest,
            review_bundle_digest=review.digest,
            input_bundle_digest=inputs.digest,
            provider_attestation_digest=attestation.digest,
            approval_set_digest=approval_set.digest,
            transparency_log_digest=digest_mapping(log.to_dict()),
            checkpoint_digest=digest_mapping(checkpoint.to_dict()),
            witness_set_digest=witness_set.digest,
            files=files,
            signer_id=signer_id,
            key_id=key_id,
            created_at=timestamp,
        )
        return cls(
            registry_digest=registry.digest,
            review_bundle_digest=review.digest,
            input_bundle_digest=inputs.digest,
            provider_attestation_digest=attestation.digest,
            approval_set_digest=approval_set.digest,
            transparency_log_digest=digest_mapping(log.to_dict()),
            checkpoint_digest=digest_mapping(checkpoint.to_dict()),
            witness_set_digest=witness_set.digest,
            files={name: dict(metadata) for name, metadata in files.items()},
            signer_id=signer_id,
            key_id=key_id,
            created_at=timestamp,
            signature=sign_mapping(private_key, unsigned),
        )

    @classmethod
    def read(cls, path: str | Path) -> "ReleaseManifest":
        payload = _read_json(path, "Governed Release Bundle")
        required = {
            "format", "version", "algorithm", "registry_digest", "review_bundle_digest",
            "input_bundle_digest", "provider_attestation_digest", "approval_set_digest",
            "transparency_log_digest", "checkpoint_digest", "witness_set_digest",
            "files", "signer", "created_at", "signature", "digest"
        }
        if set(payload) != required:
            raise ReviewError("Governed Release Bundle 欄位集合不符合 v1.0。")
        if payload.get("format") != RELEASE_MANIFEST_FORMAT or str(payload.get("version")) != RELEASE_VERSION:
            raise ReviewError("Governed Release Bundle format 或 version 不相容。")
        if payload.get("algorithm") != ALGORITHM:
            raise ReviewError("Governed Release Bundle algorithm 不相容。")
        digest_fields = (
            "registry_digest", "review_bundle_digest", "input_bundle_digest",
            "provider_attestation_digest", "approval_set_digest", "transparency_log_digest",
            "checkpoint_digest", "witness_set_digest",
        )
        digests = {field: payload.get(field) for field in digest_fields}
        if not all(_is_sha256(value) for value in digests.values()):
            raise ReviewError("Governed Release Bundle binding digest 無效。")
        raw_files = payload.get("files")
        if not isinstance(raw_files, Mapping) or not raw_files:
            raise ReviewError("Governed Release Bundle files 必須是非空 JSON object。")
        files: dict[str, dict[str, Any]] = {}
        for name, metadata in raw_files.items():
            if not isinstance(name, str) or not _safe_relative_path(name):
                raise ReviewError(f"Governed Release Bundle file path 無效：{name}")
            if not isinstance(metadata, Mapping) or set(metadata) != {"sha256", "size"}:
                raise ReviewError(f"Governed Release Bundle file metadata 無效：{name}")
            sha = metadata.get("sha256")
            size = metadata.get("size")
            if not _is_sha256(sha) or isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise ReviewError(f"Governed Release Bundle file digest/size 無效：{name}")
            files[name] = {"sha256": sha, "size": size}
        signer = payload.get("signer")
        created_at = payload.get("created_at")
        signature = payload.get("signature")
        digest = payload.get("digest")
        if not isinstance(signer, Mapping) or set(signer) != {"id", "key_id"}:
            raise ReviewError("Governed Release Bundle signer metadata 無效。")
        signer_id = signer.get("id")
        key_id = signer.get("key_id")
        if not isinstance(signer_id, str) or not signer_id.strip() or not isinstance(key_id, str):
            raise ReviewError("Governed Release Bundle signer identity 無效。")
        _validate_key_id(key_id)
        if not isinstance(created_at, str) or not isinstance(signature, str):
            raise ReviewError("Governed Release Bundle created_at 或 signature 無效。")
        _validate_timestamp(created_at)
        if not _is_sha256(digest):
            raise ReviewError("Governed Release Bundle digest 無效。")
        manifest = cls(
            registry_digest=digests["registry_digest"],
            review_bundle_digest=digests["review_bundle_digest"],
            input_bundle_digest=digests["input_bundle_digest"],
            provider_attestation_digest=digests["provider_attestation_digest"],
            approval_set_digest=digests["approval_set_digest"],
            transparency_log_digest=digests["transparency_log_digest"],
            checkpoint_digest=digests["checkpoint_digest"],
            witness_set_digest=digests["witness_set_digest"],
            files=files,
            signer_id=signer_id.strip(),
            key_id=key_id,
            created_at=created_at,
            signature=signature,
        )
        if not hmac.compare_digest(manifest.digest, digest):
            raise ReviewError("Governed Release Bundle canonical digest 不一致。")
        return manifest

    def unsigned_dict(self) -> dict[str, Any]:
        return _release_unsigned(
            registry_digest=self.registry_digest,
            review_bundle_digest=self.review_bundle_digest,
            input_bundle_digest=self.input_bundle_digest,
            provider_attestation_digest=self.provider_attestation_digest,
            approval_set_digest=self.approval_set_digest,
            transparency_log_digest=self.transparency_log_digest,
            checkpoint_digest=self.checkpoint_digest,
            witness_set_digest=self.witness_set_digest,
            files=self.files,
            signer_id=self.signer_id,
            key_id=self.key_id,
            created_at=self.created_at,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = self.unsigned_dict()
        payload["signature"] = self.signature
        payload["digest"] = self.digest
        return payload

    @property
    def digest(self) -> str:
        return digest_mapping({**self.unsigned_dict(), "signature": self.signature})

    def verify_signature(self, public_key: Ed25519PublicKey) -> None:
        if not hmac.compare_digest(self.key_id, public_key_id(public_key)):
            raise ReviewError("Governed Release Bundle key_id 與 public key 不一致。")
        verify_mapping(public_key, self.unsigned_dict(), self.signature)

    def write(self, path: str | Path) -> None:
        _atomic_write_json(Path(path), self.to_dict())


@dataclass(frozen=True, slots=True)
class GovernedReleaseBundle:
    root: Path
    manifest: ReleaseManifest

    @classmethod
    def build(
        cls,
        *,
        output_dir: str | Path,
        registry: TrustRegistry,
        root_public_key: Ed25519PublicKey,
        review: ReviewBundle,
        inputs: InputBundle,
        attestation: ProviderAttestation,
        approval_set: ApprovalSet,
        log: TransparencyLog,
        checkpoint: TransparencyCheckpoint,
        witness_set: WitnessSet,
        release_signer: str,
        release_private_key: Ed25519PrivateKey,
        created_at: str | None = None,
    ) -> "GovernedReleaseBundle":
        registry.verify(root_public_key)
        _verify_governance_chain(
            registry=registry,
            review=review,
            inputs=inputs,
            attestation=attestation,
            approval_set=approval_set,
            log=log,
            checkpoint=checkpoint,
            witness_set=witness_set,
        )
        release_entry = registry.resolve(
            public_key_id(release_private_key.public_key()),
            role="release",
            principal=release_signer,
        )
        target = Path(output_dir).resolve()
        if target.exists() and any(target.iterdir()):
            raise ReviewError(f"Governed Release output 目錄必須為空：{target}")
        target.mkdir(parents=True, exist_ok=True)
        review_target = target / "review"
        input_target = target / "input"
        governance_target = target / "governance"
        governance_target.mkdir(parents=True, exist_ok=True)
        _copy_review(review, review_target)
        inputs.copy_to(input_target)
        registry.write(governance_target / "trust-registry.json")
        attestation.write(governance_target / "provider-attestation.json")
        approval_set.write(governance_target / "approval-set.json")
        _atomic_write_json(governance_target / "transparency-log.json", log.to_dict())
        checkpoint.write(governance_target / "transparency-checkpoint.json")
        witness_set.write(governance_target / "witness-set.json")
        files = _collect_files(target)
        manifest = ReleaseManifest.create(
            registry=registry,
            review=review,
            inputs=inputs,
            attestation=attestation,
            approval_set=approval_set,
            log=log,
            checkpoint=checkpoint,
            witness_set=witness_set,
            files=files,
            signer=release_entry.principal,
            private_key=release_private_key,
            created_at=created_at,
        )
        manifest.write(target / "release-bundle.json")
        bundle = cls(root=target, manifest=manifest)
        bundle.verify(root_public_key)
        return bundle

    @classmethod
    def read(cls, path: str | Path) -> "GovernedReleaseBundle":
        candidate = Path(path)
        manifest_path = candidate / "release-bundle.json" if candidate.is_dir() else candidate
        manifest = ReleaseManifest.read(manifest_path)
        return cls(root=manifest_path.resolve().parent, manifest=manifest)

    @property
    def digest(self) -> str:
        return self.manifest.digest

    def verify(self, root_public_key: Ed25519PublicKey) -> None:
        self._verify_files()
        registry = TrustRegistry.read(self.root / "governance/trust-registry.json")
        registry.verify(root_public_key)
        if not hmac.compare_digest(self.manifest.registry_digest, registry.digest):
            raise ReviewError("Release Manifest 綁定的是另一份 Trust Registry。")
        release_entry = registry.resolve(
            self.manifest.key_id,
            role="release",
            principal=self.manifest.signer_id,
        )
        self.manifest.verify_signature(release_entry.public_key())
        review = ReviewBundle.read(self.root / "review/review-bundle.json")
        inputs = InputBundle.read(self.root / "input/input-bundle.json")
        attestation = ProviderAttestation.read(self.root / "governance/provider-attestation.json")
        approval_set = ApprovalSet.read(self.root / "governance/approval-set.json")
        log = TransparencyLog.read(self.root / "governance/transparency-log.json")
        checkpoint = TransparencyCheckpoint.read(self.root / "governance/transparency-checkpoint.json")
        witness_set = WitnessSet.read(self.root / "governance/witness-set.json")
        _verify_manifest_bindings(
            self.manifest,
            review=review,
            inputs=inputs,
            attestation=attestation,
            approval_set=approval_set,
            log=log,
            checkpoint=checkpoint,
            witness_set=witness_set,
        )
        _verify_governance_chain(
            registry=registry,
            review=review,
            inputs=inputs,
            attestation=attestation,
            approval_set=approval_set,
            log=log,
            checkpoint=checkpoint,
            witness_set=witness_set,
        )

    def load_components(self) -> dict[str, Any]:
        return {
            "registry": TrustRegistry.read(self.root / "governance/trust-registry.json"),
            "review": ReviewBundle.read(self.root / "review/review-bundle.json"),
            "inputs": InputBundle.read(self.root / "input/input-bundle.json"),
            "attestation": ProviderAttestation.read(self.root / "governance/provider-attestation.json"),
            "approval_set": ApprovalSet.read(self.root / "governance/approval-set.json"),
            "log": TransparencyLog.read(self.root / "governance/transparency-log.json"),
            "checkpoint": TransparencyCheckpoint.read(self.root / "governance/transparency-checkpoint.json"),
            "witness_set": WitnessSet.read(self.root / "governance/witness-set.json"),
        }

    def _verify_files(self) -> None:
        expected = set(self.manifest.files)
        actual = {
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
            if path.is_file() and path.name != "release-bundle.json"
        }
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise ReviewError(f"Governed Release file 集合不一致：missing={missing}; extra={extra}")
        for name, metadata in self.manifest.files.items():
            path = (self.root / name).resolve()
            if not path.is_relative_to(self.root) or path.is_symlink() or not path.is_file():
                raise ReviewError(f"Governed Release file 遺失或不安全：{name}")
            if path.stat().st_size != metadata["size"]:
                raise ReviewError(f"Governed Release file size 已變更：{name}")
            if not hmac.compare_digest(sha256_file(path), str(metadata["sha256"])):
                raise ReviewError(f"Governed Release file SHA-256 已變更：{name}")


def _verify_governance_chain(
    *,
    registry: TrustRegistry,
    review: ReviewBundle,
    inputs: InputBundle,
    attestation: ProviderAttestation,
    approval_set: ApprovalSet,
    log: TransparencyLog,
    checkpoint: TransparencyCheckpoint,
    witness_set: WitnessSet,
) -> None:
    review.verify_files()
    inputs.verify()
    proposal = ProviderProposal.read(review.root / "provider-proposal.json")
    provider_entry = registry.resolve(
        attestation.key_id,
        role="provider",
        principal=attestation.provider_id,
    )
    attestation.verify(proposal, provider_entry.public_key())
    if not log.contains(event="provider-attested", subject=attestation.digest):
        raise ReviewError("Transparency Log 缺少目前 Provider Attestation。")
    if log.key_revoked(attestation.key_id):
        raise ReviewError(f"Provider key 已在 Transparency Log 中撤銷：{attestation.key_id}")
    approval_set.verify(review, inputs, attestation, registry, log)
    checkpoint_entry = registry.resolve(
        checkpoint.key_id,
        role="checkpoint",
        principal=checkpoint.signer_id,
    )
    checkpoint.verify(log, checkpoint_entry.public_key())
    if log.key_revoked(checkpoint.key_id):
        raise ReviewError(f"Checkpoint key 已在 Transparency Log 中撤銷：{checkpoint.key_id}")
    witness_set.verify(checkpoint, registry, log=log)


def _verify_manifest_bindings(
    manifest: ReleaseManifest,
    *,
    review: ReviewBundle,
    inputs: InputBundle,
    attestation: ProviderAttestation,
    approval_set: ApprovalSet,
    log: TransparencyLog,
    checkpoint: TransparencyCheckpoint,
    witness_set: WitnessSet,
) -> None:
    bindings = (
        (manifest.review_bundle_digest, review.digest, "Review Bundle"),
        (manifest.input_bundle_digest, inputs.digest, "Input Bundle"),
        (manifest.provider_attestation_digest, attestation.digest, "Provider Attestation"),
        (manifest.approval_set_digest, approval_set.digest, "Approval Set"),
        (manifest.transparency_log_digest, digest_mapping(log.to_dict()), "Transparency Log"),
        (manifest.checkpoint_digest, digest_mapping(checkpoint.to_dict()), "Checkpoint"),
        (manifest.witness_set_digest, witness_set.digest, "Witness Set"),
    )
    for recorded, actual, label in bindings:
        if not hmac.compare_digest(recorded, actual):
            raise ReviewError(f"Release Manifest 綁定的是另一個 {label}。")


def _copy_review(review: ReviewBundle, target: Path) -> None:
    review.verify_files()
    target.mkdir(parents=True, exist_ok=True)
    for name, metadata in review.files.items():
        source = (review.root / name).resolve()
        destination = (target / name).resolve()
        if not destination.is_relative_to(target):
            raise ReviewError(f"Review Bundle file path 超出 release 目錄：{name}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if destination.stat().st_size != metadata["size"] or sha256_file(destination) != metadata["sha256"]:
            raise ReviewError(f"Review Bundle release copy 驗證失敗：{name}")
    shutil.copyfile(review.root / "review-bundle.json", target / "review-bundle.json")
    copied = ReviewBundle.read(target / "review-bundle.json")
    if not hmac.compare_digest(copied.digest, review.digest):
        raise ReviewError("Review Bundle release copy digest 不一致。")


def _collect_files(root: Path) -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative == "release-bundle.json":
            continue
        files[relative] = {"sha256": sha256_file(path), "size": path.stat().st_size}
    if not files:
        raise ReviewError("Governed Release Bundle 不可為空。")
    return files


def _release_unsigned(
    *,
    registry_digest: str,
    review_bundle_digest: str,
    input_bundle_digest: str,
    provider_attestation_digest: str,
    approval_set_digest: str,
    transparency_log_digest: str,
    checkpoint_digest: str,
    witness_set_digest: str,
    files: Mapping[str, Mapping[str, Any]],
    signer_id: str,
    key_id: str,
    created_at: str,
) -> dict[str, Any]:
    return {
        "format": RELEASE_MANIFEST_FORMAT,
        "version": RELEASE_VERSION,
        "algorithm": ALGORITHM,
        "registry_digest": registry_digest,
        "review_bundle_digest": review_bundle_digest,
        "input_bundle_digest": input_bundle_digest,
        "provider_attestation_digest": provider_attestation_digest,
        "approval_set_digest": approval_set_digest,
        "transparency_log_digest": transparency_log_digest,
        "checkpoint_digest": checkpoint_digest,
        "witness_set_digest": witness_set_digest,
        "files": {name: dict(files[name]) for name in sorted(files)},
        "signer": {"id": signer_id, "key_id": key_id},
        "created_at": created_at,
    }


def _safe_relative_path(value: str) -> bool:
    path = Path(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and not value.startswith(("/", "\\"))


def _validate_key_id(value: str) -> None:
    if not value.startswith("sha256:") or not _is_sha256(value[7:]):
        raise ReviewError("Release key_id 必須是 sha256:<64 hex>。")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_timestamp(value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewError("Release timestamp 不是合法 ISO-8601。") from exc
    if parsed.tzinfo is None:
        raise ReviewError("Release timestamp 必須包含 timezone。")


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
