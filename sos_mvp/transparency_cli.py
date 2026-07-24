from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from .review import ReviewError
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
        prog="ulcs-log",
        description="ULCS v0.9 hash-chained Transparency Log and signed checkpoints",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    append = subparsers.add_parser("append")
    append.add_argument("log")
    append.add_argument("event")
    append.add_argument("subject")
    append.add_argument("--metadata", help="JSON object 字串")
    append.add_argument("--expected-head")
    append.add_argument("--json", action="store_true")

    revoke = subparsers.add_parser("revoke-key")
    revoke.add_argument("log")
    revoke.add_argument("key_id")
    revoke.add_argument("--reason", required=True)
    revoke.add_argument("--expected-head")
    revoke.add_argument("--json", action="store_true")

    verify = subparsers.add_parser("verify")
    verify.add_argument("log")
    verify.add_argument("--expected-head")
    verify.add_argument("--json", action="store_true")

    head = subparsers.add_parser("head")
    head.add_argument("log")
    head.add_argument("--json", action="store_true")

    checkpoint = subparsers.add_parser("checkpoint")
    checkpoint.add_argument("log")
    checkpoint.add_argument("--signer", required=True)
    checkpoint.add_argument("--private-key", required=True)
    checkpoint.add_argument("--output", required=True)
    checkpoint.add_argument("--json", action="store_true")

    verify_checkpoint = subparsers.add_parser("verify-checkpoint")
    verify_checkpoint.add_argument("log")
    verify_checkpoint.add_argument("checkpoint")
    verify_checkpoint.add_argument("--public-key", required=True)
    verify_checkpoint.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    args = build_parser().parse_args(argv)
    try:
        result = _dispatch(args)
    except (OSError, ReviewError, TypeError, ValueError) as exc:
        print(f"[Transparency Log 拒絕] {exc}", file=sys.stderr)
        return 4
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[Transparency Log 通過] {result['action']}")
        for key, value in result.items():
            if key != "action":
                print(f"{key}：{value}")
    return 0


def _dispatch(args: argparse.Namespace) -> dict[str, object]:
    if args.command == "append":
        metadata = _metadata(args.metadata)
        log = TransparencyLog.read(args.log, create=True).append(
            event=args.event,
            subject=args.subject,
            metadata=metadata,
            expected_head=args.expected_head,
        )
        return _log_result("append", log)

    if args.command == "revoke-key":
        log = TransparencyLog.read(args.log, create=True).append(
            event="key-revoked",
            subject=args.key_id,
            metadata={"reason": args.reason},
            expected_head=args.expected_head,
        )
        return _log_result("key-revoked", log)

    if args.command == "verify":
        log = TransparencyLog.read(args.log)
        log.verify(expected_head=args.expected_head)
        return _log_result("verified", log)

    if args.command == "head":
        log = TransparencyLog.read(args.log)
        log.verify()
        return _log_result("head", log)

    if args.command == "checkpoint":
        log = TransparencyLog.read(args.log)
        private_key = load_private_key(args.private_key)
        checkpoint = TransparencyCheckpoint.create(log, private_key, signer=args.signer)
        checkpoint.write(args.output)
        return {
            "action": "checkpoint-created",
            "log_head": checkpoint.log_head,
            "entry_count": checkpoint.entry_count,
            "key_id": checkpoint.key_id,
            "output": str(Path(args.output).resolve()),
        }

    log = TransparencyLog.read(args.log)
    checkpoint = TransparencyCheckpoint.read(args.checkpoint)
    public_key = load_public_key(args.public_key)
    checkpoint.verify(log, public_key)
    return {
        "action": "checkpoint-verified",
        "log_head": checkpoint.log_head,
        "entry_count": checkpoint.entry_count,
        "signer": checkpoint.signer_id,
        "key_id": checkpoint.key_id,
    }


def _metadata(raw: str | None) -> Mapping[str, Any]:
    if raw is None:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReviewError(f"--metadata 不是合法 JSON：{exc}") from exc
    if not isinstance(payload, Mapping):
        raise ReviewError("--metadata 必須是 JSON object。")
    return payload


def _log_result(action: str, log: TransparencyLog) -> dict[str, object]:
    return {
        "action": action,
        "path": str(log.path),
        "entry_count": len(log.entries),
        "head_digest": log.head_digest,
    }


if __name__ == "__main__":
    raise SystemExit(main())
