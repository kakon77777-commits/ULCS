from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .intent import (
    IntentCompileError,
    IntentRequest,
    compile_intent,
    supported_profiles,
)


def _configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ulcs-intent",
        description=(
            "ULCS v0.7 Intent Compiler：將受支援的自然語言意圖編譯為"
            "可審查的 .sos、Artifact Contract、能力政策與驗證報告"
        ),
    )
    parser.add_argument("request", nargs="?", help="ULCS-Intent-Request JSON 路徑")
    parser.add_argument("--text", help="直接提供自然語言意圖")
    parser.add_argument("--profile", choices=supported_profiles(), help="明確指定編譯 profile")
    parser.add_argument(
        "--binding",
        action="append",
        default=[],
        metavar="KEY=JSON",
        help="補充具體輸入，例如 terms=[\"ERROR\",\"FATAL\"]；可重複",
    )
    parser.add_argument(
        "--preference",
        action="append",
        default=[],
        metavar="KEY=JSON",
        help="補充輸出偏好，例如 include_matches=false；可重複",
    )
    parser.add_argument(
        "--output-dir",
        default="output/intent-v0.7",
        help="生成 bundle 的目錄",
    )
    parser.add_argument("--no-write", action="store_true", help="只顯示計畫，不寫入 bundle")
    parser.add_argument("--json", action="store_true", help="輸出完整 Intent Bundle JSON")
    parser.add_argument("--list-profiles", action="store_true", help="列出目前支援的 profile")
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    args = build_parser().parse_args(argv)

    if args.list_profiles:
        print("\n".join(supported_profiles()))
        return 0
    if bool(args.request) == bool(args.text):
        print("[參數錯誤] 必須且只能提供 request JSON 或 --text。", file=sys.stderr)
        return 2

    try:
        base = (
            IntentRequest.read(args.request)
            if args.request
            else IntentRequest(intent=args.text, source="command-line")
        )
        bindings = dict(base.bindings)
        preferences = dict(base.preferences)
        bindings.update(_parse_assignments(args.binding, "binding"))
        preferences.update(_parse_assignments(args.preference, "preference"))
        request = IntentRequest(
            intent=base.intent,
            profile=args.profile or base.profile,
            bindings=bindings,
            preferences=preferences,
            source=base.source,
        )
        bundle = compile_intent(request)
        written = {} if args.no_write else bundle.write(Path(args.output_dir))
    except (OSError, IntentCompileError, TypeError, ValueError) as exc:
        print(f"[意圖編譯失敗] {exc}", file=sys.stderr)
        return 3

    if args.json:
        payload = bundle.to_dict()
        if written:
            payload["written_files"] = written
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("=== ULCS v0.7 Intent Compiler ===")
        print(f"狀態：{bundle.status}")
        print(f"Profile：{bundle.profile or 'unresolved'}")
        print(f"信心：{bundle.confidence:.2f}")
        print(f"來源：{bundle.request.source}")
        if bundle.assumptions:
            print("--- 假設 ---")
            for item in bundle.assumptions:
                print(f"- {item}")
        if bundle.missing_fields:
            print("--- 需要補充 ---")
            for item in bundle.missing_fields:
                print(f"- {item}")
        if bundle.risks:
            print("--- 風險 ---")
            for item in bundle.risks:
                print(f"- {item}")
        if bundle.ready:
            claims = bundle.validation.get("required_claims", [])
            print(f"能力需求：{', '.join(claims) if claims else 'pure'}")
            print(f"Program digest：{bundle.validation.get('program_digest')}")
            print(f"Plan digest：{bundle.validation.get('plan_digest')}")
        if written:
            print("--- 產物 ---")
            for label, path in written.items():
                print(f"{label}: {path}")

    if bundle.status == "ready":
        return 0
    if bundle.status == "needs_clarification":
        return 4
    return 5


def _parse_assignments(values: list[str], label: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw in values:
        if "=" not in raw:
            raise IntentCompileError(f"{label} 必須使用 KEY=JSON：{raw!r}")
        key, encoded = raw.split("=", 1)
        key = key.strip()
        if not key or any(ch.isspace() for ch in key):
            raise IntentCompileError(f"{label} key 不可為空或包含空白：{key!r}")
        try:
            value = json.loads(encoded)
        except json.JSONDecodeError:
            value = encoded
        result[key] = value
    return result


if __name__ == "__main__":
    raise SystemExit(main())
