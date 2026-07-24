from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping

from .attestations_v09 import ProviderAttestation, SignedApproval
from .cli import main as runtime_main
from .inputs_v09 import InputBundle
from .review import ProviderProposal, ReviewBundle, ReviewError, sha256_file
from .transparency_v09 import TransparencyCheckpoint, TransparencyLog
from .v09_crypto import load_public_key

_BLOCKED_RUNTIME_OPTIONS = (
    "--allow",
    "--cache-dir",
    "--cache-mode",
    "--checkpoint",
    "--contract",
    "--cwd",
    "--db",
    "--deny",
    "--enforce-capabilities",
    "--max-nodes",
    "--max-output-bytes",
    "--max-total-output-bytes",
    "--max-workers",
    "--plugin",
    "--policy",
    "--resume",
    "--verify-manifest",
)


def _configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ulcs-trusted",
        description=(
            "ULCS v0.9 Trusted Runner：驗證 Review、Input、Provider Attestation、"
            "Ed25519 Approval 與 Transparency Checkpoint 後執行快照"
        ),
        allow_abbrev=False,
    )
    parser.add_argument("review", help="review-bundle.json 或其所在目錄")
    parser.add_argument("inputs", help="input-bundle.json 或其所在目錄")
    parser.add_argument("attestation", help="Provider Attestation JSON")
    parser.add_argument("approval", help="Signed Approval JSON")
    parser.add_argument("--provider-public-key", required=True)
    parser.add_argument("--approver-public-key", required=True)
    parser.add_argument("--transparency-log", required=True)
    parser.add_argument("--log-checkpoint", required=True)
    parser.add_argument("--checkpoint-public-key", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    args, runtime_args = build_parser().parse_known_args(argv)
    runtime_args = list(runtime_args)
    if runtime_args[:1] == ["--"]:
        runtime_args = runtime_args[1:]
    try:
        _reject_runtime_overrides(runtime_args)
        review = ReviewBundle.read(args.review)
        inputs = InputBundle.read(args.inputs)
        proposal = ProviderProposal.read(review.root / "provider-proposal.json")
        attestation = ProviderAttestation.read(args.attestation)
        approval = SignedApproval.read(args.approval)
        provider_public = load_public_key(args.provider_public_key)
        approver_public = load_public_key(args.approver_public_key)
        checkpoint_public = load_public_key(args.checkpoint_public_key)
        attestation.verify(proposal, provider_public)
        approval.verify(review, inputs, attestation, approver_public, required_scope="execute")
        log = TransparencyLog.read(args.transparency_log)
        checkpoint = TransparencyCheckpoint.read(args.log_checkpoint)
        checkpoint.verify(log, checkpoint_public)
        _verify_log_governance(log, checkpoint, attestation, approval)
    except (OSError, ReviewError, TypeError, ValueError) as exc:
        print(f"[Trusted Runner 拒絕] {exc}", file=sys.stderr)
        return 4

    print(
        f"[Trusted Gate 通過] provider={attestation.provider_id}; "
        f"approver={approval.approver_id}; review={review.digest[:16]}; "
        f"inputs={inputs.digest[:16]}; log={checkpoint.log_head[:16]}"
    )
    with tempfile.TemporaryDirectory(prefix="ulcs-trusted-") as temp_name:
        snapshot = Path(temp_name).resolve()
        try:
            snapshot_review = _copy_review(review, snapshot)
            snapshot_inputs = inputs.copy_to(snapshot)
            governance_dir = snapshot / ".ulcs-governance"
            governance_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(args.attestation, governance_dir / "provider-attestation.json")
            shutil.copyfile(args.approval, governance_dir / "signed-approval.json")
            shutil.copyfile(args.transparency_log, governance_dir / "transparency-log.json")
            shutil.copyfile(args.log_checkpoint, governance_dir / "transparency-checkpoint.json")
        except (OSError, ReviewError) as exc:
            print(f"[Trusted Runner 拒絕] 無法建立可信快照：{exc}", file=sys.stderr)
            return 4
        delegated = [
            str(snapshot / "workflow.sos"),
            "--cwd",
            str(snapshot),
            "--policy",
            str(snapshot / "capability-policy.json"),
            "--contract",
            str(snapshot / "artifact-contract.json"),
            *runtime_args,
        ]
        environment = {
            "ULCS_INPUT_ROOT": str(snapshot / "inputs"),
            "ULCS_INPUT_BUNDLE": str(snapshot / "input-bundle.json"),
            "ULCS_REVIEW_BUNDLE_DIGEST": snapshot_review.digest,
            "ULCS_INPUT_BUNDLE_DIGEST": snapshot_inputs.digest,
            "ULCS_PROVIDER_ATTESTATION_DIGEST": attestation.digest,
            "ULCS_SIGNED_APPROVAL_DIGEST": approval.digest,
            "ULCS_TRANSPARENCY_HEAD": checkpoint.log_head,
        }
        with _temporary_environment(environment):
            return runtime_main(delegated)


def _copy_review(review: ReviewBundle, snapshot: Path) -> ReviewBundle:
    review.verify_files()
    for name, metadata in review.files.items():
        source = review.root / name
        target = snapshot / name
        target.write_bytes(source.read_bytes())
        if target.stat().st_size != metadata["size"]:
            raise ReviewError(f"Review 快照大小不一致：{name}")
        if sha256_file(target) != metadata["sha256"]:
            raise ReviewError(f"Review 快照摘要不一致：{name}")
    manifest_source = review.root / "review-bundle.json"
    manifest_target = snapshot / "review-bundle.json"
    manifest_target.write_bytes(manifest_source.read_bytes())
    copied = ReviewBundle.read(manifest_target)
    if copied.digest != review.digest:
        raise ReviewError("Review Bundle 快照 digest 不一致。")
    return copied


def _verify_log_governance(
    log: TransparencyLog,
    checkpoint: TransparencyCheckpoint,
    attestation: ProviderAttestation,
    approval: SignedApproval,
) -> None:
    if not log.contains(event="provider-attested", subject=attestation.digest):
        raise ReviewError("Transparency Log 缺少目前 Provider Attestation。")
    if not log.contains(event="approval-issued", subject=approval.digest):
        raise ReviewError("Transparency Log 缺少目前 Signed Approval。")
    for label, key_id in (
        ("Provider", attestation.key_id),
        ("Approver", approval.key_id),
        ("Checkpoint signer", checkpoint.key_id),
    ):
        if log.key_revoked(key_id):
            raise ReviewError(f"{label} key 已在 Transparency Log 中撤銷：{key_id}")


def _reject_runtime_overrides(arguments: list[str]) -> None:
    for token in arguments:
        if not token.startswith("--"):
            continue
        for blocked in _BLOCKED_RUNTIME_OPTIONS:
            if token == blocked or token.startswith(blocked + "=") or blocked.startswith(token):
                raise ReviewError(f"Trusted Runner 不允許覆寫治理參數：{token}")


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


if __name__ == "__main__":
    raise SystemExit(main())
