from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sos_mvp.attestations_v09 import ProviderAttestation, SignedApproval
from sos_mvp.inputs_v09 import InputBundle, InputContract
from sos_mvp.review import (
    ProviderProposal,
    ReviewBundle,
    ReviewError,
    compile_provider_proposal,
)
from sos_mvp.transparency_v09 import TransparencyCheckpoint, TransparencyLog
from sos_mvp.trusted_cli import _verify_log_governance, main as trusted_main
from sos_mvp.v09_crypto import generate_keypair, load_private_key


def _proposal_payload() -> dict[str, object]:
    return {
        "format": "ULCS-Intent-Provider-Proposal",
        "version": "0.8",
        "provider": {"id": "provider-v09", "model": "fixture-v09"},
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


def _write_contract(root: Path, *, text: str | None = None) -> Path:
    source_dir = root / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "sample.log").write_text(
        text or "ERROR one\nINFO two\nFATAL three\nERROR four\n",
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


def _build_signed_stack(root: Path) -> tuple[
    ProviderProposal,
    ReviewBundle,
    InputBundle,
    ProviderAttestation,
    SignedApproval,
    Ed25519PrivateKey,
    Ed25519PrivateKey,
]:
    proposal, review = _build_review(root / "review")
    inputs = InputContract.read(_write_contract(root)).capture(root / "input")
    provider_key = Ed25519PrivateKey.generate()
    approver_key = Ed25519PrivateKey.generate()
    attestation = ProviderAttestation.create(proposal, provider_key)
    approval = SignedApproval.create(
        review,
        inputs,
        attestation,
        approver_key,
        decision="approve",
        approver="reviewer-v09",
        scopes=("execute",),
    )
    return proposal, review, inputs, attestation, approval, provider_key, approver_key


class InputContractTests(unittest.TestCase):
    def test_capture_and_verify_content_addressed_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = InputContract.read(_write_contract(root))
            bundle = contract.capture(root / "bundle")
            loaded = InputBundle.read(root / "bundle")
            self.assertEqual(loaded.digest, bundle.digest)
            text = (root / "bundle/inputs/sample.log").read_text(encoding="utf-8")
            self.assertEqual(text.count("ERROR"), 2)

    def test_changed_input_invalidates_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = InputContract.read(_write_contract(root)).capture(root / "bundle")
            (bundle.root / "inputs/sample.log").write_text("ERROR changed\n", encoding="utf-8")
            with self.assertRaises(ReviewError):
                InputBundle.read(bundle.root)

    def test_source_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contract.json"
            path.write_text(
                json.dumps(
                    {
                        "format": "ULCS-Input-Contract",
                        "version": "0.9",
                        "entries": [
                            {
                                "name": "escape",
                                "kind": "file",
                                "source": "../secret.txt",
                                "mount": "inputs/secret.txt",
                                "media_type": "text/plain",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ReviewError):
                InputContract.read(path)


class SignatureTests(unittest.TestCase):
    def test_provider_attestation_verifies_and_wrong_key_fails(self) -> None:
        proposal = ProviderProposal.from_mapping(_proposal_payload())
        provider_key = Ed25519PrivateKey.generate()
        wrong_key = Ed25519PrivateKey.generate()
        attestation = ProviderAttestation.create(
            proposal,
            provider_key,
            issued_at="2026-07-23T00:00:00+00:00",
        )
        attestation.verify(proposal, provider_key.public_key())
        with self.assertRaises(ReviewError):
            attestation.verify(proposal, wrong_key.public_key())

    def test_signed_approval_binds_exact_input_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (
                _proposal,
                review,
                inputs,
                attestation,
                approval,
                _provider_key,
                approver_key,
            ) = _build_signed_stack(root)
            approval.verify(review, inputs, attestation, approver_key.public_key())

            changed_contract = _write_contract(
                root / "other",
                text="ERROR replacement\nFATAL replacement\n",
            )
            changed_inputs = InputContract.read(changed_contract).capture(root / "other-input")
            self.assertNotEqual(changed_inputs.digest, inputs.digest)
            with self.assertRaises(ReviewError):
                approval.verify(
                    review,
                    changed_inputs,
                    attestation,
                    approver_key.public_key(),
                )

    def test_reject_signature_never_authorizes_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proposal, review = _build_review(root / "review")
            inputs = InputContract.read(_write_contract(root)).capture(root / "input")
            provider_key = Ed25519PrivateKey.generate()
            approver_key = Ed25519PrivateKey.generate()
            attestation = ProviderAttestation.create(proposal, provider_key)
            approval = SignedApproval.create(
                review,
                inputs,
                attestation,
                approver_key,
                decision="reject",
                approver="reviewer-v09",
                scopes=("review",),
            )
            with self.assertRaises(ReviewError):
                approval.verify(review, inputs, attestation, approver_key.public_key())


class TransparencyTests(unittest.TestCase):
    def test_hash_chain_checkpoint_and_tamper_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.json"
            log = TransparencyLog.read(path, create=True)
            log = log.append(event="provider-attested", subject="a" * 64)
            log = log.append(event="approval-issued", subject="b" * 64)
            key = Ed25519PrivateKey.generate()
            checkpoint = TransparencyCheckpoint.create(log, key, signer="log-operator")
            checkpoint.verify(log, key.public_key())
            self.assertEqual(checkpoint.entry_count, 2)

            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["entries"][0]["subject"] = "tampered"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ReviewError):
                TransparencyLog.read(path)

    def test_revoked_approver_key_blocks_governance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (
                _proposal,
                _review,
                _inputs,
                attestation,
                approval,
                _provider_key,
                _approver_key,
            ) = _build_signed_stack(root)
            log = TransparencyLog.read(root / "log.json", create=True)
            log = log.append(event="provider-attested", subject=attestation.digest)
            log = log.append(event="approval-issued", subject=approval.digest)
            log = log.append(
                event="key-revoked",
                subject=approval.key_id,
                metadata={"reason": "compromised"},
            )
            checkpoint_key = Ed25519PrivateKey.generate()
            checkpoint = TransparencyCheckpoint.create(log, checkpoint_key, signer="operator")
            with self.assertRaises(ReviewError):
                _verify_log_governance(log, checkpoint, attestation, approval)


class TrustedRunnerTests(unittest.TestCase):
    def test_trusted_runner_executes_only_verified_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proposal, review = _build_review(root / "review")
            inputs = InputContract.read(_write_contract(root)).capture(root / "input")

            provider_private = root / "keys/provider-private.pem"
            provider_public = root / "keys/provider-public.pem"
            approver_private = root / "keys/approver-private.pem"
            approver_public = root / "keys/approver-public.pem"
            checkpoint_private = root / "keys/checkpoint-private.pem"
            checkpoint_public = root / "keys/checkpoint-public.pem"
            generate_keypair(provider_private, provider_public)
            generate_keypair(approver_private, approver_public)
            generate_keypair(checkpoint_private, checkpoint_public)

            attestation = ProviderAttestation.create(
                proposal,
                load_private_key(provider_private),
            )
            attestation_path = root / "provider-attestation.json"
            attestation.write(attestation_path)
            approval = SignedApproval.create(
                review,
                inputs,
                attestation,
                load_private_key(approver_private),
                decision="approve",
                approver="reviewer-v09",
            )
            approval_path = root / "signed-approval.json"
            approval.write(approval_path)

            log = TransparencyLog.read(root / "transparency-log.json", create=True)
            log = log.append(event="provider-attested", subject=attestation.digest)
            log = log.append(event="approval-issued", subject=approval.digest)
            checkpoint = TransparencyCheckpoint.create(
                log,
                load_private_key(checkpoint_private),
                signer="log-operator",
            )
            checkpoint_path = root / "checkpoint.json"
            checkpoint.write(checkpoint_path)

            inspected: dict[str, object] = {}

            def inspect_runtime(arguments: list[str]) -> int:
                snapshot = Path(arguments[arguments.index("--cwd") + 1])
                inspected["input"] = (snapshot / "inputs/sample.log").read_text(
                    encoding="utf-8"
                )
                inspected["workflow"] = (snapshot / "workflow.sos").read_text(
                    encoding="utf-8"
                )
                inspected["args"] = arguments
                return 0

            with patch("sos_mvp.trusted_cli.runtime_main", side_effect=inspect_runtime):
                result = trusted_main(
                    [
                        str(review.root / "review-bundle.json"),
                        str(inputs.root / "input-bundle.json"),
                        str(attestation_path),
                        str(approval_path),
                        "--provider-public-key",
                        str(provider_public),
                        "--approver-public-key",
                        str(approver_public),
                        "--transparency-log",
                        str(log.path),
                        "--log-checkpoint",
                        str(checkpoint_path),
                        "--checkpoint-public-key",
                        str(checkpoint_public),
                        "--",
                        "--yes",
                        "--json",
                    ]
                )
            self.assertEqual(result, 0)
            self.assertIn("ERROR four", str(inspected["input"]))
            self.assertIn("Get-ChildItem './inputs'", str(inspected["workflow"]))
            self.assertIn("--policy", inspected["args"])


if __name__ == "__main__":
    unittest.main()
