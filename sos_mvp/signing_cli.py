from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .attestations_v09 import ProviderAttestation, SignedApproval
from .inputs_v09 import InputBundle
from .review import ProviderProposal, ReviewBundle, ReviewError
from .transparency_v09 import TransparencyLog
from .v09_crypto import generate_keypair, load_private_key, load_public_key


def _configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ulcs-sign",
        description="ULCS v0.9 Ed25519 Provider Attestation and Signed Approval",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    keygen = subparsers.add_parser("keygen")
    keygen.add_argument("--private-key", required=True)
    keygen.add_argument("--public-key", required=True)
    keygen.add_argument("--json", action="store_true")

    provider = subparsers.add_parser("provider")
    provider.add_argument("proposal", help="provider-proposal.json")
    provider.add_argument("--private-key", required=True)
    provider.add_argument("--output", required=True)
    provider.add_argument("--log", help="附加 provider-attested 事件至 Transparency Log")
    provider.add_argument("--json", action="store_true")

    verify_provider = subparsers.add_parser("verify-provider")
    verify_provider.add_argument("proposal")
    verify_provider.add_argument("attestation")
    verify_provider.add_argument("--public-key", required=True)
    verify_provider.add_argument("--json", action="store_true")

    for command in ("approve", "reject"):
        child = subparsers.add_parser(command)
        child.add_argument("review", help="review-bundle.json 或其所在目錄")
        child.add_argument("inputs", help="input-bundle.json 或其所在目錄")
        child.add_argument("attestation", help="Provider Attestation JSON")
        child.add_argument("--provider-public-key", required=True)
        child.add_argument("--private-key", required=True, help="Approver Ed25519 private key")
        child.add_argument("--approver", required=True)
        child.add_argument("--scope", action="append", default=[])
        child.add_argument("--reason", default="")
        child.add_argument("--output", required=True)
        child.add_argument("--log", help="附加 approval-issued 或 approval-rejected 事件")
        child.add_argument("--json", action="store_true")

    verify_approval = subparsers.add_parser("verify-approval")
    verify_approval.add_argument("review")
    verify_approval.add_argument("inputs")
    verify_approval.add_argument("attestation")
    verify_approval.add_argument("approval")
    verify_approval.add_argument("--provider-public-key", required=True)
    verify_approval.add_argument("--approver-public-key", required=True)
    verify_approval.add_argument("--scope", default="execute")
    verify_approval.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    args = build_parser().parse_args(argv)
    try:
        result = _dispatch(args)
    except (OSError, ReviewError, TypeError, ValueError) as exc:
        print(f"[v0.9 Signature Gate 拒絕] {exc}", file=sys.stderr)
        return 4
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[v0.9 Signature Gate 通過] {result['action']}")
        for key, value in result.items():
            if key != "action":
                print(f"{key}：{value}")
    return 0


def _dispatch(args: argparse.Namespace) -> dict[str, object]:
    if args.command == "keygen":
        key_id = generate_keypair(args.private_key, args.public_key)
        return {
            "action": "keygen",
            "key_id": key_id,
            "private_key": str(Path(args.private_key).resolve()),
            "public_key": str(Path(args.public_key).resolve()),
        }

    if args.command == "provider":
        proposal = ProviderProposal.read(args.proposal)
        private_key = load_private_key(args.private_key)
        attestation = ProviderAttestation.create(proposal, private_key)
        attestation.write(args.output)
        log_head = None
        if args.log:
            log = TransparencyLog.read(args.log, create=True).append(
                event="provider-attested",
                subject=attestation.digest,
                metadata={
                    "provider_id": attestation.provider_id,
                    "model": attestation.model,
                    "key_id": attestation.key_id,
                    "proposal_digest": attestation.proposal_digest,
                },
            )
            log_head = log.head_digest
        return {
            "action": "provider-attested",
            "attestation_digest": attestation.digest,
            "proposal_digest": attestation.proposal_digest,
            "key_id": attestation.key_id,
            "output": str(Path(args.output).resolve()),
            "log_head": log_head,
        }

    if args.command == "verify-provider":
        proposal = ProviderProposal.read(args.proposal)
        attestation = ProviderAttestation.read(args.attestation)
        public_key = load_public_key(args.public_key)
        attestation.verify(proposal, public_key)
        return {
            "action": "provider-verified",
            "attestation_digest": attestation.digest,
            "proposal_digest": proposal.digest,
            "key_id": attestation.key_id,
        }

    review = ReviewBundle.read(args.review)
    inputs = InputBundle.read(args.inputs)
    attestation = ProviderAttestation.read(args.attestation)
    proposal = ProviderProposal.read(review.root / "provider-proposal.json")
    provider_public_key = load_public_key(args.provider_public_key)
    attestation.verify(proposal, provider_public_key)

    if args.command in {"approve", "reject"}:
        private_key = load_private_key(args.private_key)
        default_scope = "execute" if args.command == "approve" else "review"
        scopes = tuple(args.scope) or (default_scope,)
        approval = SignedApproval.create(
            review,
            inputs,
            attestation,
            private_key,
            decision=args.command,
            approver=args.approver,
            scopes=scopes,
            reason=args.reason,
        )
        approval.write(args.output)
        log_head = None
        if args.log:
            event = "approval-issued" if args.command == "approve" else "approval-rejected"
            log = TransparencyLog.read(args.log, create=True).append(
                event=event,
                subject=approval.digest,
                metadata={
                    "approver": approval.approver_id,
                    "key_id": approval.key_id,
                    "decision": approval.decision,
                    "review_bundle_digest": approval.review_bundle_digest,
                    "input_bundle_digest": approval.input_bundle_digest,
                    "provider_attestation_digest": approval.provider_attestation_digest,
                },
            )
            log_head = log.head_digest
        return {
            "action": "signed-" + args.command,
            "approval_digest": approval.digest,
            "decision": approval.decision,
            "key_id": approval.key_id,
            "output": str(Path(args.output).resolve()),
            "log_head": log_head,
        }

    approval = SignedApproval.read(args.approval)
    approver_public_key = load_public_key(args.approver_public_key)
    approval.verify(
        review,
        inputs,
        attestation,
        approver_public_key,
        required_scope=args.scope,
    )
    return {
        "action": "approval-verified",
        "approval_digest": approval.digest,
        "review_bundle_digest": review.digest,
        "input_bundle_digest": inputs.digest,
        "provider_attestation_digest": attestation.digest,
        "approver": approval.approver_id,
        "key_id": approval.key_id,
    }


if __name__ == "__main__":
    raise SystemExit(main())
