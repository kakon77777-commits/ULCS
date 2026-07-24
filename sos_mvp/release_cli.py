from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from .attestations_v09 import ProviderAttestation
from .governance_v10 import ApprovalSet, TrustRegistry
from .inputs_v09 import InputBundle
from .release_v10 import GovernedReleaseBundle
from .review import ReviewBundle, ReviewError
from .transparency_v09 import TransparencyCheckpoint, TransparencyLog
from .trusted_cli import main as trusted_main
from .v09_crypto import load_private_key, load_public_key


def _configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ulcs-release",
        description="ULCS v1.0 Governed Release Bundle build、verify 與 execute",
        allow_abbrev=False,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="建立 self-contained Governed Release Bundle", allow_abbrev=False)
    build.add_argument("review")
    build.add_argument("inputs")
    build.add_argument("attestation")
    build.add_argument("approval_set")
    build.add_argument("transparency_log")
    build.add_argument("checkpoint")
    build.add_argument("witness_set")
    build.add_argument("registry")
    build.add_argument("--root-public-key", required=True)
    build.add_argument("--private-key", required=True)
    build.add_argument("--signer", required=True)
    build.add_argument("--created-at")
    build.add_argument("--output-dir", required=True)
    build.add_argument("--json", action="store_true")

    verify = sub.add_parser("verify", help="驗證完整 Governed Release Bundle", allow_abbrev=False)
    verify.add_argument("bundle")
    verify.add_argument("--root-public-key", required=True)
    verify.add_argument("--json", action="store_true")

    execute = sub.add_parser("execute", help="驗證後透過既有 Trusted Runner 執行", allow_abbrev=False)
    execute.add_argument("bundle")
    execute.add_argument("--root-public-key", required=True)
    execute.add_argument("runtime_args", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    args = build_parser().parse_args(argv)
    try:
        root_public = load_public_key(args.root_public_key)
        if args.command == "build":
            bundle = GovernedReleaseBundle.build(
                output_dir=args.output_dir,
                registry=TrustRegistry.read(args.registry),
                root_public_key=root_public,
                review=ReviewBundle.read(args.review),
                inputs=InputBundle.read(args.inputs),
                attestation=ProviderAttestation.read(args.attestation),
                approval_set=ApprovalSet.read(args.approval_set),
                log=TransparencyLog.read(args.transparency_log),
                checkpoint=TransparencyCheckpoint.read(args.checkpoint),
                witness_set=__import__("sos_mvp.governance_v10", fromlist=["WitnessSet"]).WitnessSet.read(args.witness_set),
                release_signer=args.signer,
                release_private_key=load_private_key(args.private_key),
                created_at=args.created_at,
            )
            _emit(
                {
                    "created": True,
                    "release_digest": bundle.digest,
                    "file_count": len(bundle.manifest.files),
                    "registry_digest": bundle.manifest.registry_digest,
                    "approval_set_digest": bundle.manifest.approval_set_digest,
                    "witness_set_digest": bundle.manifest.witness_set_digest,
                    "output": str(bundle.root),
                },
                args.json,
            )
            return 0
        bundle = GovernedReleaseBundle.read(args.bundle)
        bundle.verify(root_public)
        if args.command == "verify":
            _emit(
                {
                    "verified": True,
                    "release_digest": bundle.digest,
                    "signer": bundle.manifest.signer_id,
                    "key_id": bundle.manifest.key_id,
                    "file_count": len(bundle.manifest.files),
                },
                args.json,
            )
            return 0
        runtime_args = list(args.runtime_args)
        if runtime_args[:1] == ["--"]:
            runtime_args = runtime_args[1:]
        return _execute(bundle, runtime_args)
    except (OSError, ReviewError, TypeError, ValueError) as exc:
        print(f"[ULCS Release 拒絕] {exc}", file=sys.stderr)
        return 4


def _execute(bundle: GovernedReleaseBundle, runtime_args: list[str]) -> int:
    components = bundle.load_components()
    registry: TrustRegistry = components["registry"]
    attestation: ProviderAttestation = components["attestation"]
    approval_set: ApprovalSet = components["approval_set"]
    checkpoint: TransparencyCheckpoint = components["checkpoint"]
    if not approval_set.approvals:
        raise ReviewError("Governed Release Approval Set 不可為空。")
    selected = approval_set.approvals[0]
    provider_entry = registry.resolve(
        attestation.key_id,
        role="provider",
        principal=attestation.provider_id,
    )
    approver_entry = registry.resolve(
        selected.key_id,
        role="approver",
        principal=selected.approver_id,
    )
    checkpoint_entry = registry.resolve(
        checkpoint.key_id,
        role="checkpoint",
        principal=checkpoint.signer_id,
    )
    with tempfile.TemporaryDirectory(prefix="ulcs-release-gate-") as temp_name:
        temp = Path(temp_name)
        provider_key = temp / "provider-public.pem"
        approver_key = temp / "approver-public.pem"
        checkpoint_key = temp / "checkpoint-public.pem"
        approval_path = temp / "selected-approval.json"
        provider_key.write_text(provider_entry.public_key_pem, encoding="ascii", newline="\n")
        approver_key.write_text(approver_entry.public_key_pem, encoding="ascii", newline="\n")
        checkpoint_key.write_text(checkpoint_entry.public_key_pem, encoding="ascii", newline="\n")
        approval_path.write_text(
            json.dumps(selected.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        delegated = [
            str(bundle.root / "review/review-bundle.json"),
            str(bundle.root / "input/input-bundle.json"),
            str(bundle.root / "governance/provider-attestation.json"),
            str(approval_path),
            "--provider-public-key",
            str(provider_key),
            "--approver-public-key",
            str(approver_key),
            "--transparency-log",
            str(bundle.root / "governance/transparency-log.json"),
            "--log-checkpoint",
            str(bundle.root / "governance/transparency-checkpoint.json"),
            "--checkpoint-public-key",
            str(checkpoint_key),
            "--",
            *runtime_args,
        ]
        environment = {
            "ULCS_RELEASE_BUNDLE_DIGEST": bundle.digest,
            "ULCS_TRUST_REGISTRY_DIGEST": registry.digest,
            "ULCS_APPROVAL_SET_DIGEST": approval_set.digest,
            "ULCS_WITNESS_SET_DIGEST": components["witness_set"].digest,
        }
        print(
            f"[Release Gate 通過] registry={registry.registry_id}; "
            f"approvals={len(approval_set.approvals)}/{approval_set.threshold}; "
            f"witnesses={len(components['witness_set'].witnesses)}/{components['witness_set'].threshold}; "
            f"release={bundle.digest[:16]}"
        )
        with _temporary_environment(environment):
            return trusted_main(delegated)


@contextmanager
def _temporary_environment(values: Mapping[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _emit(payload: dict[str, Any], json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    raise SystemExit(main())
