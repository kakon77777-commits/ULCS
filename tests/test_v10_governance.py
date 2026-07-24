from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sos_mvp.attestations_v09 import ProviderAttestation, SignedApproval
from sos_mvp.governance_v10 import (
    ApprovalSet,
    RegistryKey,
    TrustPolicy,
    TrustRegistry,
    WitnessSet,
    WitnessStatement,
)
from sos_mvp.inputs_v09 import InputContract
from sos_mvp.release_cli import main as release_main
from sos_mvp.release_v10 import GovernedReleaseBundle
from sos_mvp.review import ProviderProposal, ReviewBundle, ReviewError, compile_provider_proposal
from sos_mvp.transparency_v09 import TransparencyCheckpoint, TransparencyLog


def _proposal_payload() -> dict[str, object]:
    return {
        "format": "ULCS-Intent-Provider-Proposal",
        "version": "0.8",
        "provider": {"id": "provider-v10", "model": "fixture-v10"},
        "request": {
            "format": "ULCS-Intent-Request",
            "version": "0.7",
            "intent": "分析受控 inputs 目錄中的日誌，找出 ERROR 與 FATAL。",
            "profile": "log-analysis",
            "bindings": {
                "source_path": "./inputs",
                "pattern": "*.log",
                "terms": ["ERROR", "FATAL"],
                "recursive": False,
            },
            "preferences": {"include_matches": True, "persist_summary": True},
        },
        "notes": [],
        "confidence": 0.99,
    }


def _write_contract(root: Path) -> Path:
    source_dir = root / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "sample.log").write_text(
        "ERROR one\nINFO two\nFATAL three\nERROR four\n",
        encoding="utf-8",
    )
    contract_path = root / "input-contract-source.json"
    contract_path.write_text(
        json.dumps(
            {
                "format": "ULCS-Input-Contract",
                "version": "0.9",
                "limits": {"max_file_bytes": 1024, "max_total_bytes": 2048},
                "entries": [
                    {
                        "name": "analysis-log",
                        "kind": "file",
                        "source": "source/sample.log",
                        "mount": "inputs/sample.log",
                        "media_type": "text/plain; charset=utf-8",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return contract_path


def _build_review(root: Path) -> tuple[ProviderProposal, ReviewBundle]:
    proposal = ProviderProposal.from_mapping(_proposal_payload())
    _, review = compile_provider_proposal(proposal, root)
    assert review is not None
    return proposal, review


def _registry_key(principal: str, role: str, private: Ed25519PrivateKey) -> RegistryKey:
    return RegistryKey.from_public_key(
        principal=principal,
        roles=(role,),
        public_key=private.public_key(),
    )


class GovernanceFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.proposal, self.review = _build_review(root / "review")
        self.inputs = InputContract.read(_write_contract(root)).capture(root / "input")
        self.root_key = Ed25519PrivateKey.generate()
        self.provider_key = Ed25519PrivateKey.generate()
        self.approver_a_key = Ed25519PrivateKey.generate()
        self.approver_b_key = Ed25519PrivateKey.generate()
        self.checkpoint_key = Ed25519PrivateKey.generate()
        self.witness_key = Ed25519PrivateKey.generate()
        self.release_key = Ed25519PrivateKey.generate()
        policy = TrustPolicy(
            approval_threshold=2,
            witness_threshold=1,
            required_approval_scopes=("execute",),
            distinct_approver_principals=True,
        )
        self.registry = TrustRegistry.create(
            registry_id="registry-v10",
            policy=policy,
            keys=(
                _registry_key("provider-v10", "provider", self.provider_key),
                _registry_key("reviewer-a", "approver", self.approver_a_key),
                _registry_key("reviewer-b", "approver", self.approver_b_key),
                _registry_key("checkpoint-operator", "checkpoint", self.checkpoint_key),
                _registry_key("witness-a", "witness", self.witness_key),
                _registry_key("release-operator", "release", self.release_key),
            ),
            root_private_key=self.root_key,
            issued_at="2026-07-24T00:00:00+00:00",
        )
        self.attestation = ProviderAttestation.create(
            self.proposal,
            self.provider_key,
            issued_at="2026-07-24T00:01:00+00:00",
        )
        self.approval_a = SignedApproval.create(
            self.review,
            self.inputs,
            self.attestation,
            self.approver_a_key,
            decision="approve",
            approver="reviewer-a",
            issued_at="2026-07-24T00:02:00+00:00",
        )
        self.approval_b = SignedApproval.create(
            self.review,
            self.inputs,
            self.attestation,
            self.approver_b_key,
            decision="approve",
            approver="reviewer-b",
            issued_at="2026-07-24T00:03:00+00:00",
        )
        self.log = TransparencyLog.read(root / "transparency-log.json", create=True)
        self.log = self.log.append(event="provider-attested", subject=self.attestation.digest)
        self.log = self.log.append(event="approval-issued", subject=self.approval_a.digest)
        self.log = self.log.append(event="approval-issued", subject=self.approval_b.digest)
        self.approval_set = ApprovalSet.create(
            self.review,
            self.inputs,
            self.attestation,
            (self.approval_a, self.approval_b),
            self.registry,
            self.log,
            created_at="2026-07-24T00:04:00+00:00",
        )
        self.checkpoint = TransparencyCheckpoint.create(
            self.log,
            self.checkpoint_key,
            signer="checkpoint-operator",
            issued_at="2026-07-24T00:05:00+00:00",
        )
        self.witness = WitnessStatement.create(
            self.checkpoint,
            self.witness_key,
            witness="witness-a",
            issued_at="2026-07-24T00:06:00+00:00",
        )
        self.witness_set = WitnessSet.create(
            self.checkpoint,
            (self.witness,),
            self.registry,
            log=self.log,
            created_at="2026-07-24T00:07:00+00:00",
        )

    def build_release(self) -> GovernedReleaseBundle:
        return GovernedReleaseBundle.build(
            output_dir=self.root / "release",
            registry=self.registry,
            root_public_key=self.root_key.public_key(),
            review=self.review,
            inputs=self.inputs,
            attestation=self.attestation,
            approval_set=self.approval_set,
            log=self.log,
            checkpoint=self.checkpoint,
            witness_set=self.witness_set,
            release_signer="release-operator",
            release_private_key=self.release_key,
            created_at="2026-07-24T00:08:00+00:00",
        )


class TrustRegistryTests(unittest.TestCase):
    def test_registry_uses_external_root_trust_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = GovernanceFixture(Path(tmp))
            fixture.registry.verify(fixture.root_key.public_key())
            with self.assertRaises(ReviewError):
                fixture.registry.verify(Ed25519PrivateKey.generate().public_key())

    def test_registry_rejects_insufficient_distinct_approvers(self) -> None:
        root_key = Ed25519PrivateKey.generate()
        provider = Ed25519PrivateKey.generate()
        approver_a = Ed25519PrivateKey.generate()
        approver_b = Ed25519PrivateKey.generate()
        checkpoint = Ed25519PrivateKey.generate()
        witness = Ed25519PrivateKey.generate()
        release = Ed25519PrivateKey.generate()
        policy = TrustPolicy(2, 1, ("execute",), True)
        with self.assertRaises(ReviewError):
            TrustRegistry.create(
                registry_id="invalid",
                policy=policy,
                keys=(
                    _registry_key("provider", "provider", provider),
                    _registry_key("same-reviewer", "approver", approver_a),
                    _registry_key("same-reviewer", "approver", approver_b),
                    _registry_key("checkpoint", "checkpoint", checkpoint),
                    _registry_key("witness", "witness", witness),
                    _registry_key("release", "release", release),
                ),
                root_private_key=root_key,
            )


class ThresholdAndWitnessTests(unittest.TestCase):
    def test_one_approval_cannot_satisfy_two_of_two_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = GovernanceFixture(Path(tmp))
            with self.assertRaises(ReviewError):
                ApprovalSet.create(
                    fixture.review,
                    fixture.inputs,
                    fixture.attestation,
                    (fixture.approval_a,),
                    fixture.registry,
                    fixture.log,
                )

    def test_witness_is_bound_to_exact_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = GovernanceFixture(Path(tmp))
            fixture.witness_set.verify(fixture.checkpoint, fixture.registry, log=fixture.log)
            changed_log = fixture.log.append(event="post-checkpoint-event", subject="changed")
            changed_checkpoint = TransparencyCheckpoint.create(
                changed_log,
                fixture.checkpoint_key,
                signer="checkpoint-operator",
            )
            with self.assertRaises(ReviewError):
                fixture.witness_set.verify(changed_checkpoint, fixture.registry, log=changed_log)


class GovernedReleaseTests(unittest.TestCase):
    def test_release_bundle_is_self_contained_and_verifiable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = GovernanceFixture(Path(tmp))
            bundle = fixture.build_release()
            loaded = GovernedReleaseBundle.read(bundle.root)
            loaded.verify(fixture.root_key.public_key())
            self.assertEqual(loaded.manifest.signer_id, "release-operator")
            self.assertGreaterEqual(len(loaded.manifest.files), 10)

    def test_tampered_release_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = GovernanceFixture(Path(tmp))
            bundle = fixture.build_release()
            target = bundle.root / "input/inputs/sample.log"
            target.write_text("ERROR tampered\n", encoding="utf-8")
            with self.assertRaises(ReviewError):
                GovernedReleaseBundle.read(bundle.root).verify(fixture.root_key.public_key())

    def test_release_execute_delegates_only_after_full_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = GovernanceFixture(Path(tmp))
            bundle = fixture.build_release()
            root_public_path = Path(tmp) / "root-public.pem"
            root_public_path.write_bytes(
                fixture.root_key.public_key().public_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PublicFormat.SubjectPublicKeyInfo,
                )
            )
            observed: dict[str, object] = {}

            def inspect(arguments: list[str]) -> int:
                observed["arguments"] = arguments
                observed["release_digest"] = os.environ.get("ULCS_RELEASE_BUNDLE_DIGEST")
                observed["registry_digest"] = os.environ.get("ULCS_TRUST_REGISTRY_DIGEST")
                return 0

            with patch("sos_mvp.release_cli.trusted_main", side_effect=inspect):
                result = release_main(
                    [
                        "execute",
                        str(bundle.root),
                        "--root-public-key",
                        str(root_public_path),
                        "--",
                        "--yes",
                        "--json",
                    ]
                )
            self.assertEqual(result, 0)
            self.assertEqual(observed["release_digest"], bundle.digest)
            self.assertEqual(observed["registry_digest"], fixture.registry.digest)
            self.assertIn("--provider-public-key", observed["arguments"])
            self.assertIn("--yes", observed["arguments"])


if __name__ == "__main__":
    unittest.main()
