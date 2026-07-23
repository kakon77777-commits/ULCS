from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .review import (
    ProviderProposal,
    ReviewError,
    compile_provider_proposal,
)


def _configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ulcs-provider",
        description=(
            "ULCS v0.8 Provider Contract：驗證外部 AI／Agent 提案，"
            "再交給確定性 Intent Compiler 建立可審查 Bundle"
        ),
    )
    parser.add_argument("proposal", help="ULCS-Intent-Provider-Proposal JSON")
    parser.add_argument(
        "--output-dir",
        default="output/provider-v0.8",
        help="Provider Proposal、Intent Bundle 與 Review Bundle 輸出目錄",
    )
    parser.add_argument("--json", action="store_true", help="輸出機器可讀結果")
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    args = build_parser().parse_args(argv)
    try:
        proposal = ProviderProposal.read(args.proposal)
        intent_bundle, review = compile_provider_proposal(
            proposal,
            Path(args.output_dir),
        )
    except (OSError, ReviewError, TypeError, ValueError) as exc:
        print(f"[Provider Proposal 拒絕] {exc}", file=sys.stderr)
        return 3

    payload = {
        "format": "ULCS-Provider-Compilation-Result",
        "version": "0.8",
        "provider": {
            "id": proposal.provider_id,
            "model": proposal.model,
            "proposal_digest": proposal.digest,
        },
        "intent_status": intent_bundle.status,
        "review_bundle": review.to_dict() if review is not None else None,
        "output_dir": str(Path(args.output_dir).resolve()),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("=== ULCS v0.8 Provider Contract ===")
        print(f"Provider：{proposal.provider_id}")
        print(f"Model：{proposal.model}")
        print(f"Proposal digest：{proposal.digest}")
        print(f"Intent 狀態：{intent_bundle.status}")
        if review is not None:
            print(f"Review digest：{review.digest}")
            print(f"Review Bundle：{review.root / 'review-bundle.json'}")
        else:
            print("Review Bundle：未建立；只有 ready 提案可進入核准流程。")

    if intent_bundle.status == "ready":
        return 0
    if intent_bundle.status == "needs_clarification":
        return 4
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
