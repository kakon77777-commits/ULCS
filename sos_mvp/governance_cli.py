from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .attestations_v09 import ProviderAttestation, SignedApproval
from .governance_v10 import (
    ApprovalSet,
    RegistryKey,
    TrustPolicy,
    TrustRegistry,
    WitnessSet,
    WitnessStatement,
    registry_key_from_file,
)
from .inputs_v09 import InputBundle
from .review import ReviewBundle, ReviewError
from .transparency_v09 import TransparencyCheckpoint, TransparencyLog
from .v09_crypto import load_private_key, load_public_key


def _configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ulcs-govern",
        description="ULCS v1.0 Trust Registry、threshold approvals 與 checkpoint witnesses",
        allow_abbrev=False,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    registry = sub.add_parser("registry", help="建立 Root-signed Trust Registry", allow_abbrev=False)
    registry.add_argument("--registry-id", required=True)
    registry.add_argument("--root-private-key", required=True)
    registry.add_argument("--provider-key", action="append", default=[], metavar="PRINCIPAL=PEM")
    registry.add_argument("--approver-key", action="append", default=[], metavar="PRINCIPAL=PEM")
    registry.add_argument("--checkpoint-key", action="append", default=[], metavar="PRINCIPAL=PEM")
    registry.add_argument("--witness-key", action="append", default=[], metavar="PRINCIPAL=PEM")
    registry.add_argument("--release-key", action="append", default=[], metavar="PRINCIPAL=PEM")
    registry.add_argument("--approval-threshold", type=int, required=True)
    registry.add_argument("--witness-threshold", type=int, required=True)
    registry.add_argument("--required-scope", action="append", default=["execute"])
    registry.add_argument("--allow-same-approver-principal", action="store_true")
    registry.add_argument("--issued-at")
    registry.add_argument("--output", required=True)
    registry.add_argument("--json", action="store_true")

    verify_registry = sub.add_parser("verify-registry", help="驗證 Trust Registry root signature", allow_abbrev=False)
    verify_registry.add_argument("registry")
    verify_registry.add_argument("--root-public-key", required=True)
    verify_registry.add_argument("--json", action="store_true")

    approvals = sub.add_parser("approvals", help="建立並驗證 threshold Approval Set", allow_abbrev=False)
    approvals.add_argument("review")
    approvals.add_argument("inputs")
    approvals.add_argument("attestation")
    approvals.add_argument("--approval", action="append", required=True)
    approvals.add_argument("--registry", required=True)
    approvals.add_argument("--root-public-key", required=True)
    approvals.add_argument("--transparency-log", required=True)
    approvals.add_argument("--created-at")
    approvals.add_argument("--output", required=True)
    approvals.add_argument("--json", action="store_true")

    witness = sub.add_parser("witness", help="對 Transparency Checkpoint 建立外部見證", allow_abbrev=False)
    witness.add_argument("checkpoint")
    witness.add_argument("--private-key", required=True)
    witness.add_argument("--witness", required=True)
    witness.add_argument("--issued-at")
    witness.add_argument("--output", required=True)
    witness.add_argument("--json", action="store_true")

    witnesses = sub.add_parser("witnesses", help="建立並驗證 Witness Set", allow_abbrev=False)
    witnesses.add_argument("checkpoint")
    witnesses.add_argument("--witness", action="append", required=True)
    witnesses.add_argument("--registry", required=True)
    witnesses.add_argument("--root-public-key", required=True)
    witnesses.add_argument("--transparency-log")
    witnesses.add_argument("--created-at")
    witnesses.add_argument("--output", required=True)
    witnesses.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    args = build_parser().parse_args(argv)
    try:
        if args.command == "registry":
            return _create_registry(args)
        if args.command == "verify-registry":
            registry = TrustRegistry.read(args.registry)
            registry.verify(load_public_key(args.root_public_key))
            _emit(
                {
                    "verified": True,
                    "registry_id": registry.registry_id,
                    "registry_digest": registry.digest,
                    "root_key_id": registry.root_key_id,
                },
                args.json,
            )
            return 0
        if args.command == "approvals":
            registry = TrustRegistry.read(args.registry)
            registry.verify(load_public_key(args.root_public_key))
            review = ReviewBundle.read(args.review)
            inputs = InputBundle.read(args.inputs)
            attestation = ProviderAttestation.read(args.attestation)
            log = TransparencyLog.read(args.transparency_log)
            approvals = [SignedApproval.read(path) for path in args.approval]
            approval_set = ApprovalSet.create(
                review,
                inputs,
                attestation,
                approvals,
                registry,
                log,
                created_at=args.created_at,
            )
            approval_set.write(args.output)
            _emit(
                {
                    "created": True,
                    "approval_set_digest": approval_set.digest,
                    "approval_count": len(approval_set.approvals),
                    "threshold": approval_set.threshold,
                    "output": str(Path(args.output).resolve()),
                },
                args.json,
            )
            return 0
        if args.command == "witness":
            checkpoint = TransparencyCheckpoint.read(args.checkpoint)
            statement = WitnessStatement.create(
                checkpoint,
                load_private_key(args.private_key),
                witness=args.witness,
                issued_at=args.issued_at,
            )
            statement.write(args.output)
            _emit(
                {
                    "created": True,
                    "witness_digest": statement.digest,
                    "witness": statement.witness_id,
                    "key_id": statement.key_id,
                    "output": str(Path(args.output).resolve()),
                },
                args.json,
            )
            return 0
        if args.command == "witnesses":
            registry = TrustRegistry.read(args.registry)
            registry.verify(load_public_key(args.root_public_key))
            checkpoint = TransparencyCheckpoint.read(args.checkpoint)
            statements = [WitnessStatement.read(path) for path in args.witness]
            log = TransparencyLog.read(args.transparency_log) if args.transparency_log else None
            witness_set = WitnessSet.create(
                checkpoint,
                statements,
                registry,
                log=log,
                created_at=args.created_at,
            )
            witness_set.write(args.output)
            _emit(
                {
                    "created": True,
                    "witness_set_digest": witness_set.digest,
                    "witness_count": len(witness_set.witnesses),
                    "threshold": witness_set.threshold,
                    "output": str(Path(args.output).resolve()),
                },
                args.json,
            )
            return 0
    except (OSError, ReviewError, TypeError, ValueError) as exc:
        print(f"[ULCS Governance 拒絕] {exc}", file=sys.stderr)
        return 4
    return 2


def _create_registry(args: argparse.Namespace) -> int:
    specs = {
        "provider": args.provider_key,
        "approver": args.approver_key,
        "checkpoint": args.checkpoint_key,
        "witness": args.witness_key,
        "release": args.release_key,
    }
    grouped: dict[str, RegistryKey] = {}
    for role, values in specs.items():
        for value in values:
            principal, path = _parse_principal_path(value)
            entry = registry_key_from_file(
                principal=principal,
                roles=(role,),
                public_key_path=path,
            )
            existing = grouped.get(entry.key_id)
            if existing is None:
                grouped[entry.key_id] = entry
                continue
            if existing.principal != entry.principal:
                raise ReviewError("同一把 Trust Registry key 不可對應不同 principal。")
            grouped[entry.key_id] = RegistryKey(
                principal=existing.principal,
                key_id=existing.key_id,
                roles=tuple(dict.fromkeys((*existing.roles, role))),
                status="active",
                public_key_pem=existing.public_key_pem,
            )
    policy = TrustPolicy.from_mapping(
        {
            "approval_threshold": args.approval_threshold,
            "witness_threshold": args.witness_threshold,
            "required_approval_scopes": list(dict.fromkeys(args.required_scope)),
            "distinct_approver_principals": not args.allow_same_approver_principal,
        }
    )
    registry = TrustRegistry.create(
        registry_id=args.registry_id,
        policy=policy,
        keys=tuple(grouped.values()),
        root_private_key=load_private_key(args.root_private_key),
        issued_at=args.issued_at,
    )
    registry.write(args.output)
    _emit(
        {
            "created": True,
            "registry_id": registry.registry_id,
            "registry_digest": registry.digest,
            "root_key_id": registry.root_key_id,
            "keys": len(registry.keys),
            "approval_threshold": registry.policy.approval_threshold,
            "witness_threshold": registry.policy.witness_threshold,
            "output": str(Path(args.output).resolve()),
        },
        args.json,
    )
    return 0


def _parse_principal_path(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise ReviewError("key 參數必須使用 PRINCIPAL=PEM_PATH。")
    principal, path = value.split("=", 1)
    if not principal.strip() or not path.strip():
        raise ReviewError("key 參數的 principal 與 path 都不可為空。")
    return principal.strip(), path.strip()


def _emit(payload: dict[str, Any], json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    raise SystemExit(main())
