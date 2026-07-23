from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .review import (
    ApprovalRecord,
    ReviewBundle,
    ReviewError,
    load_key,
    verify_approval,
)


def _configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _add_key_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--key-env",
        help="從指定環境變數讀取 HMAC key；不接受命令列明文 key",
    )
    group.add_argument(
        "--key-file",
        help="從檔案讀取 HMAC key；key 不會寫入 Approval Record",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ulcs-approve",
        description="ULCS v0.8 Approval Gate 與 HMAC-SHA256 完整性核准",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("approve", "reject"):
        child = subparsers.add_parser(command)
        child.add_argument("review", help="review-bundle.json 或其所在目錄")
        child.add_argument("--approver", required=True, help="核准／拒絕者識別字串")
        child.add_argument(
            "--scope",
            action="append",
            default=[],
            help="核准 scope，可重複；approve 預設 execute，reject 預設 review",
        )
        child.add_argument("--reason", default="", help="決策原因")
        child.add_argument(
            "--output",
            default="approval.json",
            help="Approval Record 輸出路徑",
        )
        _add_key_arguments(child)

    verify = subparsers.add_parser("verify")
    verify.add_argument("review", help="review-bundle.json 或其所在目錄")
    verify.add_argument("approval", help="Approval Record JSON")
    verify.add_argument("--scope", default="execute", help="要求的核准 scope")
    verify.add_argument("--json", action="store_true", help="輸出機器可讀結果")
    _add_key_arguments(verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    args = build_parser().parse_args(argv)
    try:
        key = load_key(env_name=args.key_env, key_file=args.key_file)
        review = ReviewBundle.read(args.review)
        if args.command in {"approve", "reject"}:
            default_scope = "execute" if args.command == "approve" else "review"
            scopes = tuple(args.scope) or (default_scope,)
            approval = ApprovalRecord.create(
                review,
                decision=args.command,
                approver=args.approver,
                key=key,
                scopes=scopes,
                reason=args.reason,
            )
            approval.write(args.output)
            print(f"[Approval Record 已寫入] {Path(args.output).resolve()}")
            print(f"決策：{approval.decision}")
            print(f"Bundle digest：{approval.bundle_digest}")
            print(f"Scopes：{', '.join(approval.scopes)}")
            return 0

        approval = ApprovalRecord.read(args.approval)
        verify_approval(
            review,
            approval,
            key=key,
            required_scope=args.scope,
        )
    except (OSError, ReviewError, TypeError, ValueError) as exc:
        print(f"[Approval Gate 拒絕] {exc}", file=sys.stderr)
        return 4

    result = {
        "verified": True,
        "bundle_digest": review.digest,
        "approver": approval.approver,
        "decision": approval.decision,
        "scopes": list(approval.scopes),
        "issued_at": approval.issued_at,
        "algorithm": "hmac-sha256",
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("[Approval Gate 通過]")
        print(f"Bundle digest：{review.digest}")
        print(f"Approver：{approval.approver}")
        print(f"Scopes：{', '.join(approval.scopes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
