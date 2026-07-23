from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from .cli import main as runtime_main
from .review import (
    ApprovalRecord,
    ReviewBundle,
    ReviewError,
    load_key,
    verify_approval,
)

_BLOCKED_RUNTIME_OPTIONS = (
    "--allow",
    "--contract",
    "--cwd",
    "--db",
    "--deny",
    "--enforce-capabilities",
    "--plugin",
    "--policy",
)


def _configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ulcs-approved",
        description=(
            "ULCS v0.8 Approved Runner：驗證 Review Bundle 與 Approval Record，"
            "從已驗證快照執行既有 ULCS runtime"
        ),
    )
    parser.add_argument("review", help="review-bundle.json 或其所在目錄")
    parser.add_argument("approval", help="Approval Record JSON")
    key_group = parser.add_mutually_exclusive_group(required=True)
    key_group.add_argument("--key-env", help="HMAC key 的環境變數名稱")
    key_group.add_argument("--key-file", help="HMAC key file")
    parser.add_argument(
        "runtime_args",
        nargs=argparse.REMAINDER,
        help="傳給 ulcs runtime 的其餘參數；可先放 -- 分隔",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    args = build_parser().parse_args(argv)
    runtime_args = list(args.runtime_args)
    if runtime_args[:1] == ["--"]:
        runtime_args = runtime_args[1:]
    try:
        _reject_runtime_overrides(runtime_args)
        key = load_key(env_name=args.key_env, key_file=args.key_file)
        review = ReviewBundle.read(args.review)
        approval = ApprovalRecord.read(args.approval)
        verify_approval(review, approval, key=key, required_scope="execute")
    except (OSError, ReviewError, ValueError) as exc:
        print(f"[Approved Runner 拒絕] {exc}", file=sys.stderr)
        return 4

    print(
        f"[Approval Gate 通過] approver={approval.approver}; "
        f"bundle={review.digest[:16]}; algorithm=hmac-sha256"
    )
    with tempfile.TemporaryDirectory(prefix="ulcs-approved-") as temp_name:
        snapshot = Path(temp_name)
        for name, metadata in review.files.items():
            source = review.root / name
            target = snapshot / name
            target.write_bytes(source.read_bytes())
            if target.stat().st_size != metadata["size"]:
                print(f"[Approved Runner 拒絕] 快照大小不一致：{name}", file=sys.stderr)
                return 4
        delegated = [
            str(snapshot / "workflow.sos"),
            "--cwd",
            str(review.root),
            "--policy",
            str(snapshot / "capability-policy.json"),
            "--contract",
            str(snapshot / "artifact-contract.json"),
            *runtime_args,
        ]
        return runtime_main(delegated)


def _reject_runtime_overrides(arguments: list[str]) -> None:
    for token in arguments:
        for blocked in _BLOCKED_RUNTIME_OPTIONS:
            if token == blocked or token.startswith(blocked + "="):
                raise ReviewError(f"Approved Runner 不允許覆寫治理參數：{blocked}")


if __name__ == "__main__":
    raise SystemExit(main())
