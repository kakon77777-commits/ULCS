from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .inputs_v09 import InputBundle, InputContract
from .review import ReviewError


def _configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ulcs-input",
        description="ULCS v0.9 Input Contract capture and verification",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture = subparsers.add_parser("capture")
    capture.add_argument("contract", help="ULCS-Input-Contract JSON")
    capture.add_argument("--output-dir", required=True, help="Input Bundle 輸出目錄")
    capture.add_argument("--json", action="store_true", help="輸出機器可讀結果")
    verify = subparsers.add_parser("verify")
    verify.add_argument("bundle", help="input-bundle.json 或其所在目錄")
    verify.add_argument("--json", action="store_true", help="輸出機器可讀結果")
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    args = build_parser().parse_args(argv)
    try:
        if args.command == "capture":
            contract = InputContract.read(args.contract)
            bundle = contract.capture(args.output_dir)
        else:
            bundle = InputBundle.read(args.bundle)
            contract = InputContract.read(bundle.root / "input-contract.json")
    except (OSError, ReviewError, TypeError, ValueError) as exc:
        print(f"[Input Contract 拒絕] {exc}", file=sys.stderr)
        return 4

    result = {
        "verified": True,
        "contract_digest": contract.digest,
        "input_bundle_digest": bundle.digest,
        "entries": [dict(item) for item in bundle.entries],
        "root": str(bundle.root),
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        action = "已建立" if args.command == "capture" else "已驗證"
        print(f"[Input Bundle {action}] {Path(bundle.root).resolve()}")
        print(f"Contract digest：{contract.digest}")
        print(f"Bundle digest：{bundle.digest}")
        print(f"Entries：{len(bundle.entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
